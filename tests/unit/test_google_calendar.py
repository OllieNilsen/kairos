"""Unit tests for Google Calendar adapter."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.google_calendar import (
    GoogleCalendarClient,
    extract_attendee_names,
    extract_attendees,
    parse_event_datetime,
)
from src.core.models import AttendeeInfo


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


class TestExtractAttendees:
    """Tests for extract_attendees helper (returns AttendeeInfo)."""

    def test_extracts_full_info(self):
        """Should extract name and email into AttendeeInfo."""
        event = {
            "attendees": [
                {"displayName": "Alice Smith", "email": "alice@example.com"},
                {"displayName": "Bob Jones", "email": "bob@example.com"},
            ]
        }

        attendees = extract_attendees(event)

        assert len(attendees) == 2
        assert attendees[0] == AttendeeInfo(name="Alice Smith", email="alice@example.com")
        assert attendees[1] == AttendeeInfo(name="Bob Jones", email="bob@example.com")

    def test_falls_back_to_email_for_name(self):
        """Should use email as name when displayName is missing."""
        event = {
            "attendees": [
                {"email": "alice@example.com"},
            ]
        }

        attendees = extract_attendees(event)

        assert len(attendees) == 1
        assert attendees[0].name == "alice@example.com"
        assert attendees[0].email == "alice@example.com"

    def test_excludes_self(self):
        """Should exclude attendees marked as self."""
        event = {
            "attendees": [
                {"displayName": "Me", "email": "me@example.com", "self": True},
                {"displayName": "Other Person", "email": "other@example.com"},
            ]
        }

        attendees = extract_attendees(event)

        assert len(attendees) == 1
        assert attendees[0].name == "Other Person"

    def test_empty_attendees(self):
        """Should return empty list when no attendees."""
        event = {}

        attendees = extract_attendees(event)

        assert attendees == []

    def test_handles_no_email(self):
        """Should handle attendees without email (rare but possible)."""
        event = {
            "attendees": [
                {"displayName": "Mystery Person"},
            ]
        }

        attendees = extract_attendees(event)

        assert len(attendees) == 1
        assert attendees[0].name == "Mystery Person"
        assert attendees[0].email is None


class TestGoogleCalendarClientEventMethods:
    """Tests for create_event, update_event, delete_event methods."""

    @pytest.fixture
    def client(self):
        """Create a client with test credentials."""
        return GoogleCalendarClient(
            client_id="test-client-id",
            client_secret="test-client-secret",
            refresh_token="test-refresh-token",
        )

    @patch("src.adapters.google_calendar.httpx.request")
    @patch("src.adapters.google_calendar.httpx.post")
    def test_create_event(self, mock_post, mock_request, client):
        """Should create a calendar event."""
        # Mock token refresh
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # Mock create response
        mock_request.return_value.json.return_value = {
            "id": "new-event-id",
            "etag": '"abc123"',
            "summary": "Test Event",
        }

        start = datetime(2025, 1, 15, 17, 30, tzinfo=UTC)
        end = datetime(2025, 1, 15, 17, 45, tzinfo=UTC)

        result = client.create_event(
            summary="Test Event",
            start_time=start,
            end_time=end,
            description="Test description",
        )

        assert result["id"] == "new-event-id"
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"  # HTTP method
        assert "events" in call_args[0][1]  # URL contains events

    @patch("src.adapters.google_calendar.httpx.request")
    @patch("src.adapters.google_calendar.httpx.post")
    def test_create_event_with_extended_properties(self, mock_post, mock_request, client):
        """Should include extended properties when provided."""
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }
        mock_request.return_value.json.return_value = {"id": "event-id"}

        start = datetime(2025, 1, 15, 17, 30, tzinfo=UTC)
        end = datetime(2025, 1, 15, 17, 45, tzinfo=UTC)

        client.create_event(
            summary="Debrief",
            start_time=start,
            end_time=end,
            extended_properties={
                "private": {"kairos_type": "debrief", "kairos_user_id": "user-001"}
            },
        )

        call_json = mock_request.call_args.kwargs["json"]
        assert "extendedProperties" in call_json
        assert call_json["extendedProperties"]["private"]["kairos_type"] == "debrief"

    @patch("src.adapters.google_calendar.httpx.request")
    @patch("src.adapters.google_calendar.httpx.post")
    def test_update_event(self, mock_post, mock_request, client):
        """Should update an existing event."""
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }

        # First call is GET (to get existing event)
        # Second call is PUT (to update)
        mock_request.return_value.json.return_value = {
            "id": "event-id",
            "summary": "Original",
            "start": {"dateTime": "2025-01-15T17:30:00Z"},
            "end": {"dateTime": "2025-01-15T17:45:00Z"},
        }

        new_start = datetime(2025, 1, 15, 18, 0, tzinfo=UTC)
        new_end = datetime(2025, 1, 15, 18, 15, tzinfo=UTC)

        client.update_event(
            event_id="event-id",
            summary="Updated Event",
            start_time=new_start,
            end_time=new_end,
        )

        # Should have called twice: GET then PUT
        assert mock_request.call_count == 2
        put_call = mock_request.call_args_list[1]
        assert put_call[0][0] == "PUT"

    @patch("src.adapters.google_calendar.httpx.delete")
    @patch("src.adapters.google_calendar.httpx.post")
    def test_delete_event_success(self, mock_post, mock_delete, client):
        """Should delete an event and return True."""
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }
        mock_delete.return_value.status_code = 204

        result = client.delete_event("event-id")

        assert result is True
        mock_delete.assert_called_once()

    @patch("src.adapters.google_calendar.httpx.delete")
    @patch("src.adapters.google_calendar.httpx.post")
    def test_delete_event_not_found(self, mock_post, mock_delete, client):
        """Should return True when event already deleted (404)."""
        mock_post.return_value.json.return_value = {
            "access_token": "test-token",
            "expires_in": 3600,
        }
        mock_delete.return_value.status_code = 404

        result = client.delete_event("event-id")

        assert result is True
