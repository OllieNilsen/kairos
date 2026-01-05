"""Unit tests for Microsoft Graph adapter."""

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.adapters.microsoft_graph import MicrosoftGraphClient


class TestMicrosoftGraphClient:
    """Tests for MicrosoftGraphClient."""

    @pytest.fixture
    def client(self):
        """Create a client with test credentials."""
        return MicrosoftGraphClient(
            client_id="test-client-id",
            client_secret="test-client-secret",
            tenant_id="test-tenant-id",
            refresh_token="test-refresh-token",
        )

    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_refresh_access_token(self, mock_post, client):
        """Should refresh access token using refresh token."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        token = client._get_access_token()

        assert token == "new-access-token"
        mock_post.assert_called_once()
        call_data = mock_post.call_args.kwargs["data"]
        assert call_data["grant_type"] == "refresh_token"
        assert call_data["refresh_token"] == "test-refresh-token"

    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_caches_access_token(self, mock_post, client):
        """Should cache access token and not refresh on subsequent calls."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "cached-token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        # First call refreshes
        token1 = client._get_access_token()
        # Second call should use cache
        token2 = client._get_access_token()

        assert token1 == token2 == "cached-token"
        assert mock_post.call_count == 1  # Only one refresh call

    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_token_refresh_on_401(self, mock_post, client):
        """Should refresh token on 401 Unauthorized error."""
        # First token response
        mock_post.return_value.json.return_value = {
            "access_token": "initial-token",
            "expires_in": 3600,
        }

        # Force token into cache
        client._get_access_token()
        assert mock_post.call_count == 1

        # Simulate 401 error requiring refresh
        client._access_token = None  # Clear cache to force refresh

        # Second token response
        mock_post.return_value.json.return_value = {
            "access_token": "refreshed-token",
            "expires_in": 3600,
        }

        token = client._get_access_token()
        assert token == "refreshed-token"
        assert mock_post.call_count == 2

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_create_subscription(self, mock_post, mock_request, client):
        """Should create subscription with clientState."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock subscription response
        mock_request.return_value.json.return_value = {
            "id": "sub-123",
            "expirationDateTime": "2025-01-08T12:00:00Z",
        }

        subscription_id, expiry, client_state = client.create_subscription(
            webhook_url="https://example.com/webhook",
            calendar_id="user-calendar-id",
        )

        assert subscription_id == "sub-123"
        assert isinstance(expiry, datetime)
        # clientState should be a valid UUID string
        assert isinstance(client_state, str)
        uuid.UUID(client_state)  # Should not raise

        # Verify API call
        mock_request.assert_called_once()
        call_json = mock_request.call_args.kwargs["json"]
        assert call_json["notificationUrl"] == "https://example.com/webhook"
        assert call_json["resource"] == "/me/calendars/user-calendar-id/events"
        assert "clientState" in call_json

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_renew_subscription(self, mock_post, mock_request, client):
        """Should renew subscription and rotate clientState."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock renewal response
        mock_request.return_value.json.return_value = {
            "id": "sub-123",
            "expirationDateTime": "2025-01-10T12:00:00Z",
        }

        expiry, new_client_state = client.renew_subscription(subscription_id="sub-123")

        assert isinstance(expiry, datetime)
        assert isinstance(new_client_state, str)
        uuid.UUID(new_client_state)  # Should be valid UUID

        # Verify PATCH request
        mock_request.assert_called_once()
        assert mock_request.call_args.args[0] == "PATCH"
        assert "/subscriptions/sub-123" in mock_request.call_args.args[1]

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_delete_subscription(self, mock_post, mock_request, client):
        """Should delete subscription."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock delete response (204 No Content)
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        client.delete_subscription(subscription_id="sub-123")

        # Verify DELETE request
        mock_request.assert_called_once()
        assert mock_request.call_args.args[0] == "DELETE"
        assert "/subscriptions/sub-123" in mock_request.call_args.args[1]

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_delta_sync(self, mock_post, mock_request, client):
        """Should process delta sync with delta link."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock delta sync response
        mock_request.return_value.json.return_value = {
            "value": [
                {"id": "event1", "subject": "Meeting 1", "changeKey": "abc123"},
                {"id": "event2", "subject": "Meeting 2", "changeKey": "def456"},
            ],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/calendar/events/delta?$deltatoken=xyz",
        }

        events, new_delta_link = client.delta_sync(
            delta_link="https://graph.microsoft.com/v1.0/me/calendar/events/delta?$deltatoken=old"
        )

        assert len(events) == 2
        assert events[0]["id"] == "event1"
        assert (
            new_delta_link
            == "https://graph.microsoft.com/v1.0/me/calendar/events/delta?$deltatoken=xyz"
        )

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_delta_sync_410_gone(self, mock_post, mock_request, client):
        """Should raise specific exception on 410 Gone (delta link expired)."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock 410 Gone response
        mock_response = MagicMock()
        mock_response.status_code = 410
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "410 Gone", request=MagicMock(), response=mock_response
        )
        mock_request.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.delta_sync(delta_link="https://graph.microsoft.com/delta?$deltatoken=expired")

        assert exc_info.value.response.status_code == 410

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_list_events(self, mock_post, mock_request, client):
        """Should list events (full sync fallback)."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock list events response
        mock_request.return_value.json.return_value = {
            "value": [
                {"id": "event1", "subject": "Meeting 1"},
                {"id": "event2", "subject": "Meeting 2"},
            ],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/calendar/events/delta?$deltatoken=initial",
        }

        events, delta_link = client.list_events(calendar_id="primary")

        assert len(events) == 2
        assert events[0]["id"] == "event1"
        assert delta_link is not None
        assert "$deltatoken=initial" in delta_link

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_retry_on_429_rate_limit(self, mock_post, mock_request, client):
        """Should retry with exponential backoff on 429 rate limit."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock 429 response then success
        mock_429_response = MagicMock()
        mock_429_response.status_code = 429
        mock_429_response.headers = {"Retry-After": "2"}
        mock_429_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429 Too Many Requests", request=MagicMock(), response=mock_429_response
        )

        mock_success_response = MagicMock()
        mock_success_response.json.return_value = {
            "value": [{"id": "event1"}],
            "@odata.deltaLink": "delta-link",
        }
        mock_success_response.raise_for_status = MagicMock()

        # First call fails with 429, second succeeds
        mock_request.side_effect = [mock_429_response, mock_success_response]

        with patch("src.adapters.microsoft_graph.time.sleep") as mock_sleep:
            events, _ = client.list_events()

            assert len(events) == 1
            # Should have slept for Retry-After value
            mock_sleep.assert_called()

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_exponential_backoff_on_transient_errors(self, mock_post, mock_request, client):
        """Should retry with exponential backoff on transient errors."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock transient error then success
        mock_error_response = MagicMock()
        mock_error_response.status_code = 503
        mock_error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable", request=MagicMock(), response=mock_error_response
        )

        mock_success_response = MagicMock()
        mock_success_response.json.return_value = {
            "value": [],
            "@odata.deltaLink": "delta-link",
        }
        mock_success_response.raise_for_status = MagicMock()

        # First call fails, second succeeds
        mock_request.side_effect = [mock_error_response, mock_success_response]

        with patch("src.adapters.microsoft_graph.time.sleep") as mock_sleep:
            events, _ = client.list_events()

            assert len(events) == 0
            # Should have slept (exponential backoff)
            mock_sleep.assert_called_once()

    @patch("src.adapters.microsoft_graph.httpx.request")
    @patch("src.adapters.microsoft_graph.httpx.post")
    def test_max_retries_exceeded(self, mock_post, mock_request, client):
        """Should raise error after max retries exceeded."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock persistent error
        mock_error_response = MagicMock()
        mock_error_response.status_code = 503
        mock_error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable", request=MagicMock(), response=mock_error_response
        )

        # All retries fail
        mock_request.return_value = mock_error_response

        with patch("src.adapters.microsoft_graph.time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                client.list_events()

            # Should have tried 4 times (initial + 3 retries)
            assert mock_request.call_count == 4
