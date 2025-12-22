"""Unit tests for Google Calendar adapter."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.google_calendar import (
    GoogleCalendarClient,
    extract_attendee_names,
    parse_event_datetime,
)


class TestGoogleCalendarClient:
    """Tests for GoogleCalendarClient."""

    @pytest.fixture
    def client(self):
        """Create a client with test credentials."""
        return GoogleCalendarClient(
            client_id="test-client-id",
            client_secret="test-client-secret",
            refresh_token="test-refresh-token",
        )

    @patch("src.adapters.google_calendar.httpx.post")
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

    @patch("src.adapters.google_calendar.httpx.post")
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

    @patch("src.adapters.google_calendar.httpx.request")
    @patch("src.adapters.google_calendar.httpx.post")
    def test_list_events(self, mock_post, mock_request, client):
        """Should list calendar events with authentication."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock events response
        mock_request.return_value.json.return_value = {
            "items": [
                {"id": "event1", "summary": "Meeting 1"},
                {"id": "event2", "summary": "Meeting 2"},
            ]
        }

        events = client.list_events()

        assert len(events) == 2
        assert events[0]["summary"] == "Meeting 1"

        # Check auth header was set
        call_headers = mock_request.call_args.kwargs["headers"]
        assert "Authorization" in call_headers
        assert call_headers["Authorization"] == "Bearer test-token"


class TestParseEventDatetime:
    """Tests for parse_event_datetime helper."""

    def test_parses_datetime_with_timezone(self):
        """Should parse dateTime format with timezone offset."""
        event = {
            "start": {"dateTime": "2025-01-15T10:00:00+00:00"},
            "end": {"dateTime": "2025-01-15T11:00:00+00:00"},
        }

        start, end = parse_event_datetime(event)

        assert start == datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        assert end == datetime(2025, 1, 15, 11, 0, tzinfo=UTC)

    def test_parses_datetime_with_z_suffix(self):
        """Should parse dateTime format with Z suffix."""
        event = {
            "start": {"dateTime": "2025-01-15T10:00:00Z"},
            "end": {"dateTime": "2025-01-15T11:00:00Z"},
        }

        start, end = parse_event_datetime(event)

        assert start is not None
        assert end is not None
        assert start.hour == 10
        assert end.hour == 11

    def test_returns_none_for_all_day_events(self):
        """Should return None for all-day events (date only)."""
        event = {
            "start": {"date": "2025-01-15"},
            "end": {"date": "2025-01-16"},
        }

        start, end = parse_event_datetime(event)

        assert start is None
        assert end is None


class TestExtractAttendeeNames:
    """Tests for extract_attendee_names helper."""

    def test_extracts_display_names(self):
        """Should extract displayName from attendees."""
        event = {
            "attendees": [
                {"displayName": "Alice Smith", "email": "alice@example.com"},
                {"displayName": "Bob Jones", "email": "bob@example.com"},
            ]
        }

        names = extract_attendee_names(event)

        assert names == ["Alice Smith", "Bob Jones"]

    def test_falls_back_to_email(self):
        """Should use email when displayName is missing."""
        event = {
            "attendees": [
                {"email": "alice@example.com"},
                {"displayName": "Bob Jones", "email": "bob@example.com"},
            ]
        }

        names = extract_attendee_names(event)

        assert names == ["alice@example.com", "Bob Jones"]

    def test_excludes_self(self):
        """Should exclude attendees marked as self."""
        event = {
            "attendees": [
                {"displayName": "Me", "email": "me@example.com", "self": True},
                {"displayName": "Other Person", "email": "other@example.com"},
            ]
        }

        names = extract_attendee_names(event)

        assert names == ["Other Person"]

    def test_empty_attendees(self):
        """Should return empty list when no attendees."""
        event = {}

        names = extract_attendee_names(event)

        assert names == []
