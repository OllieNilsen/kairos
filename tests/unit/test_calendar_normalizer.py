"""Unit tests for calendar event normalizer."""

from datetime import UTC, datetime

import pytest

from src.adapters.calendar_normalizer import normalize_google_event
from src.core.models import KairosCalendarEvent


class TestNormalizeGoogleEvent:
    """Tests for normalize_google_event function."""

    def test_basic_event_normalization(self):
        """Should normalize a basic Google Calendar event to KCNF."""
        google_event = {
            "id": "event123",
            "summary": "Team Standup",
            "description": "Daily standup meeting",
            "location": "Conference Room A",
            "status": "confirmed",
            "start": {
                "dateTime": "2025-01-05T10:00:00-05:00",
                "timeZone": "America/New_York",
            },
            "end": {
                "dateTime": "2025-01-05T10:30:00-05:00",
                "timeZone": "America/New_York",
            },
            "etag": "etag123",
            "updated": "2025-01-05T09:00:00Z",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert isinstance(result, KairosCalendarEvent)
        assert result.user_id == "user123"
        assert result.provider == "google"
        assert result.provider_event_id == "event123"
        assert result.provider_etag == "etag123"
        assert result.provider_version == "etag123"
        assert result.title == "Team Standup"
        assert result.description == "Daily standup meeting"
        assert result.location == "Conference Room A"
        assert result.status == "confirmed"
        assert result.is_all_day is False
        assert result.item_type == "event"

    def test_all_day_event(self):
        """Should handle all-day events with date field."""
        google_event = {
            "id": "allday123",
            "summary": "Team Offsite",
            "start": {"date": "2025-01-05"},
            "end": {"date": "2025-01-06"},
            "status": "confirmed",
            "etag": "etag456",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.is_all_day is True
        assert result.start.year == 2025
        assert result.start.month == 1
        assert result.start.day == 5
        assert result.start.tzinfo == UTC

    def test_attendees_extraction(self):
        """Should extract attendees with names and emails."""
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
            "attendees": [
                {"email": "alice@example.com", "displayName": "Alice Smith"},
                {"email": "bob@example.com", "displayName": "Bob Jones"},
                {"email": "charlie@example.com"},  # No displayName
            ],
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert len(result.attendees) == 3
        assert result.attendees[0].name == "Alice Smith"
        assert result.attendees[0].email == "alice@example.com"
        assert result.attendees[1].name == "Bob Jones"
        assert result.attendees[1].email == "bob@example.com"
        assert result.attendees[2].name == "charlie"  # Derived from email
        assert result.attendees[2].email == "charlie@example.com"

    def test_attendees_capped_at_200(self):
        """Should cap attendees at 200 for item size protection."""
        google_event = {
            "id": "event123",
            "summary": "Large Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
            "attendees": [
                {"email": f"user{i}@example.com", "displayName": f"User {i}"} for i in range(300)
            ],
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert len(result.attendees) == 200

    def test_organizer_extraction(self):
        """Should extract organizer information."""
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
            "organizer": {
                "email": "organizer@example.com",
                "displayName": "Event Organizer",
            },
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.organizer is not None
        assert result.organizer.name == "Event Organizer"
        assert result.organizer.email == "organizer@example.com"

    def test_conference_data_extraction(self):
        """Should extract conference/video call information."""
        google_event = {
            "id": "event123",
            "summary": "Video Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
            "conferenceData": {
                "conferenceId": "conf123",
                "entryPoints": [
                    {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
                    {"entryPointType": "phone", "uri": "tel:+1-555-0100"},
                ],
            },
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.conference is not None
        assert result.conference.join_url == "https://meet.google.com/abc-defg-hij"
        assert result.conference.conference_id == "conf123"
        assert result.conference.phone == "tel:+1-555-0100"

    def test_hangout_link_fallback(self):
        """Should fallback to hangoutLink if no conferenceData."""
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
            "hangoutLink": "https://meet.google.com/legacy-link",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.conference is not None
        assert result.conference.join_url == "https://meet.google.com/legacy-link"

    def test_recurring_event_series_master(self):
        """Should extract recurrence info from series master."""
        google_event = {
            "id": "series123",
            "summary": "Weekly Standup",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T10:30:00Z"},
            "etag": "etag123",
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"],
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.recurrence is not None
        assert result.recurrence.recurrence_rule == "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"
        assert result.recurrence.is_recurring_instance is False
        assert result.recurrence.is_exception is False

    def test_recurring_event_instance(self):
        """Should extract recurrence info from instance."""
        google_event = {
            "id": "instance123",
            "summary": "Weekly Standup",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T10:30:00Z"},
            "etag": "etag123",
            "recurringEventId": "series123",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.recurrence is not None
        assert result.recurrence.provider_series_id == "series123"
        assert result.recurrence.provider_instance_id == "instance123"
        assert result.recurrence.is_recurring_instance is True
        assert result.recurrence.is_exception is False

    def test_recurring_event_exception(self):
        """Should detect exceptions (moved instances)."""
        google_event = {
            "id": "exception123",
            "summary": "Weekly Standup (Moved)",
            "start": {"dateTime": "2025-01-05T14:00:00Z"},
            "end": {"dateTime": "2025-01-05T14:30:00Z"},
            "etag": "etag123",
            "recurringEventId": "series123",
            "originalStartTime": {"dateTime": "2025-01-05T10:00:00Z"},
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.recurrence is not None
        assert result.recurrence.is_exception is True
        assert result.recurrence.original_start is not None
        assert result.recurrence.original_start.hour == 10

    def test_kairos_debrief_event_detection(self):
        """Should detect Kairos-created debrief events."""
        google_event = {
            "id": "debrief123",
            "summary": "📞 Kairos Debrief",
            "start": {"dateTime": "2025-01-05T17:30:00Z"},
            "end": {"dateTime": "2025-01-05T17:45:00Z"},
            "etag": "etag123",
            "extendedProperties": {
                "private": {
                    "kairos_type": "debrief",
                    "kairos_user_id": "user123",
                    "kairos_date": "2025-01-05",
                }
            },
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.is_debrief_event is True
        assert result.kairos_tags["type"] == "debrief"
        assert result.kairos_tags["user_id"] == "user123"
        assert result.kairos_tags["date"] == "2025-01-05"

    def test_description_truncated_at_8kb(self):
        """Should truncate description to 8KB max."""
        large_description = "A" * 10000  # 10KB
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "description": large_description,
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.description is not None
        assert len(result.description.encode("utf-8")) <= 8 * 1024
        assert result.description.endswith("...")

    def test_ttl_for_regular_event(self):
        """Should set TTL to 180 days for regular events."""
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
        }

        result = normalize_google_event(google_event, user_id="user123")

        expected_ttl = int(result.end.timestamp()) + (180 * 24 * 60 * 60)
        assert result.ttl == expected_ttl

    def test_ttl_for_debrief_event(self):
        """Should set TTL to 365 days for debrief events."""
        google_event = {
            "id": "debrief123",
            "summary": "Debrief",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
            "extendedProperties": {"private": {"kairos_type": "debrief"}},
        }

        result = normalize_google_event(google_event, user_id="user123")

        expected_ttl = int(result.end.timestamp()) + (365 * 24 * 60 * 60)
        assert result.ttl == expected_ttl

    def test_provider_version_uses_etag(self):
        """Should use etag as provider_version."""
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": 'W/"etag123"',
            "updated": "2025-01-05T09:00:00Z",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.provider_version == 'W/"etag123"'
        assert result.provider_etag == 'W/"etag123"'
        assert result.provider_change_key is None

    def test_provider_version_fallback_to_updated(self):
        """Should fallback to updated timestamp if no etag."""
        ingested_at = datetime(2025, 1, 5, 10, 0, 0, tzinfo=UTC)
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "updated": "2025-01-05T09:00:00Z",
        }

        result = normalize_google_event(google_event, user_id="user123", ingested_at=ingested_at)

        assert result.provider_version == "2025-01-05T09:00:00Z"

    def test_missing_required_field_raises_error(self):
        """Should raise ValueError if event ID is missing."""
        google_event = {
            "summary": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
        }

        with pytest.raises(ValueError, match="Event missing required field: id"):
            normalize_google_event(google_event, user_id="user123")

    def test_invalid_datetime_raises_error(self):
        """Should raise ValueError if start/end times are invalid."""
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "start": {},  # Missing dateTime/date
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
        }

        with pytest.raises(ValueError, match="Invalid start/end time"):
            normalize_google_event(google_event, user_id="user123")

    def test_no_attendees_empty_list(self):
        """Should handle events with no attendees."""
        google_event = {
            "id": "event123",
            "summary": "Solo Task",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.attendees == []

    def test_no_organizer_returns_none(self):
        """Should handle events with no organizer."""
        google_event = {
            "id": "event123",
            "summary": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.organizer is None

    def test_no_conference_data_returns_none(self):
        """Should handle events with no conference data."""
        google_event = {
            "id": "event123",
            "summary": "In-Person Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00Z"},
            "end": {"dateTime": "2025-01-05T11:00:00Z"},
            "etag": "etag123",
        }

        result = normalize_google_event(google_event, user_id="user123")

        assert result.conference is None
