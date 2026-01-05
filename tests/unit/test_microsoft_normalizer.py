"""Unit tests for Microsoft Graph event normalizer."""

from datetime import UTC

import pytest

from src.adapters.calendar_normalizer import normalize_microsoft_event
from src.core.models import KairosCalendarEvent


class TestNormalizeMicrosoftEvent:
    """Tests for normalize_microsoft_event function."""

    def test_basic_event_normalization(self):
        """Should normalize a basic Microsoft Graph event to KCNF."""
        ms_event = {
            "id": "event123",
            "subject": "Team Standup",
            "bodyPreview": "Daily standup meeting",
            "location": {"displayName": "Conference Room A"},
            "responseStatus": {"response": "accepted"},
            "start": {
                "dateTime": "2025-01-05T10:00:00",
                "timeZone": "Eastern Standard Time",
            },
            "end": {
                "dateTime": "2025-01-05T10:30:00",
                "timeZone": "Eastern Standard Time",
            },
            "changeKey": "changekey123",
            "lastModifiedDateTime": "2025-01-05T09:00:00Z",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert isinstance(result, KairosCalendarEvent)
        assert result.user_id == "user123"
        assert result.provider == "microsoft"
        assert result.provider_event_id == "event123"
        assert result.provider_change_key == "changekey123"
        assert result.provider_version == "changekey123"
        assert result.title == "Team Standup"
        assert result.description == "Daily standup meeting"
        assert result.location == "Conference Room A"
        assert result.status == "confirmed"
        assert result.is_all_day is False
        assert result.item_type == "event"

    def test_all_day_event(self):
        """Should handle all-day events."""
        ms_event = {
            "id": "allday123",
            "subject": "Team Offsite",
            "isAllDay": True,
            "start": {
                "dateTime": "2025-01-05T00:00:00.0000000",
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": "2025-01-06T00:00:00.0000000",
                "timeZone": "UTC",
            },
            "changeKey": "key456",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.is_all_day is True
        assert result.start.year == 2025
        assert result.start.month == 1
        assert result.start.day == 5

    def test_timezone_conversion_utc(self):
        """Should convert UTC timezone correctly."""
        ms_event = {
            "id": "event123",
            "subject": "UTC Meeting",
            "start": {
                "dateTime": "2025-01-05T14:00:00.0000000",
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": "2025-01-05T15:00:00.0000000",
                "timeZone": "UTC",
            },
            "changeKey": "key123",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.start.tzinfo == UTC
        assert result.start.hour == 14
        assert result.end.tzinfo == UTC

    def test_timezone_conversion_est(self):
        """Should convert Eastern Standard Time correctly."""
        ms_event = {
            "id": "event123",
            "subject": "EST Meeting",
            "start": {
                "dateTime": "2025-01-05T10:00:00.0000000",
                "timeZone": "Eastern Standard Time",
            },
            "end": {
                "dateTime": "2025-01-05T11:00:00.0000000",
                "timeZone": "Eastern Standard Time",
            },
            "changeKey": "key123",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        # 10:00 EST = 15:00 UTC (EST is UTC-5)
        assert result.start.hour == 15
        assert result.start.tzinfo == UTC

    def test_timezone_conversion_london(self):
        """Should convert GMT Standard Time correctly."""
        ms_event = {
            "id": "event123",
            "subject": "London Meeting",
            "start": {
                "dateTime": "2025-01-05T14:00:00.0000000",
                "timeZone": "GMT Standard Time",
            },
            "end": {
                "dateTime": "2025-01-05T15:00:00.0000000",
                "timeZone": "GMT Standard Time",
            },
            "changeKey": "key123",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        # 14:00 GMT = 14:00 UTC (GMT is UTC+0)
        assert result.start.hour == 14
        assert result.start.tzinfo == UTC

    def test_attendees_extraction(self):
        """Should extract attendees with names and emails."""
        ms_event = {
            "id": "event123",
            "subject": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T11:00:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "attendees": [
                {
                    "emailAddress": {"name": "Alice Smith", "address": "alice@example.com"},
                    "status": {"response": "accepted"},
                    "type": "required",
                },
                {
                    "emailAddress": {"name": "Bob Jones", "address": "bob@example.com"},
                    "status": {"response": "tentative"},
                    "type": "optional",
                },
                {
                    "emailAddress": {"address": "charlie@example.com"},  # No name
                    "status": {"response": "needsAction"},
                },
            ],
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert len(result.attendees) == 3
        assert result.attendees[0].name == "Alice Smith"
        assert result.attendees[0].email == "alice@example.com"
        assert result.attendees[1].name == "Bob Jones"
        assert result.attendees[1].email == "bob@example.com"
        assert result.attendees[2].name == "charlie"  # Email prefix as fallback
        assert result.attendees[2].email == "charlie@example.com"

    def test_attendees_capped_at_200(self):
        """Should cap attendees at 200 for item size protection."""
        attendees = [
            {
                "emailAddress": {"name": f"User {i}", "address": f"user{i}@example.com"},
                "status": {"response": "accepted"},
            }
            for i in range(300)
        ]
        ms_event = {
            "id": "event123",
            "subject": "Large Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T11:00:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "attendees": attendees,
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert len(result.attendees) == 200

    def test_organizer_extraction(self):
        """Should extract organizer information."""
        ms_event = {
            "id": "event123",
            "subject": "Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T11:00:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "organizer": {
                "emailAddress": {
                    "name": "Jane Organizer",
                    "address": "jane@example.com",
                }
            },
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.organizer is not None
        assert result.organizer.name == "Jane Organizer"
        assert result.organizer.email == "jane@example.com"

    def test_conference_info_teams_meeting(self):
        """Should extract Teams meeting join URL."""
        ms_event = {
            "id": "event123",
            "subject": "Teams Meeting",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T11:00:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "onlineMeeting": {
                "joinUrl": "https://teams.microsoft.com/l/meetup-join/...",
                "conferenceId": "123456789",
            },
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.conference is not None
        assert result.conference.join_url == "https://teams.microsoft.com/l/meetup-join/..."
        assert result.conference.conference_id == "123456789"

    def test_description_truncation(self):
        """Should truncate description to 8KB."""
        large_description = "x" * (9 * 1024)  # 9KB
        ms_event = {
            "id": "event123",
            "subject": "Meeting",
            "bodyPreview": large_description,
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T11:00:00", "timeZone": "UTC"},
            "changeKey": "key123",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        # Should be truncated to ~8KB
        assert len(result.description.encode("utf-8")) <= 8 * 1024
        assert result.description.endswith("...")

    def test_recurrence_series_master(self):
        """Should detect series master events."""
        ms_event = {
            "id": "series123",
            "subject": "Weekly Standup",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T10:30:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "type": "seriesMaster",
            "recurrence": {
                "pattern": {
                    "type": "weekly",
                    "interval": 1,
                    "daysOfWeek": ["monday"],
                }
            },
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.recurrence is not None
        assert result.recurrence.provider_series_id == "series123"
        assert result.recurrence.is_recurring_instance is False
        assert result.recurrence.is_exception is False

    def test_recurrence_instance(self):
        """Should detect recurring instances."""
        ms_event = {
            "id": "instance123",
            "subject": "Weekly Standup",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T10:30:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "type": "occurrence",
            "seriesMasterId": "series123",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.recurrence is not None
        assert result.recurrence.provider_series_id == "series123"
        assert result.recurrence.provider_instance_id == "instance123"
        assert result.recurrence.is_recurring_instance is True
        assert result.recurrence.is_exception is False

    def test_recurrence_exception(self):
        """Should detect modified instances (exceptions)."""
        ms_event = {
            "id": "exception123",
            "subject": "Weekly Standup (Moved)",
            "start": {"dateTime": "2025-01-05T15:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T15:30:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "type": "exception",
            "seriesMasterId": "series123",
            "originalStart": "2025-01-05T10:00:00",
            "originalStartTimeZone": "UTC",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.recurrence is not None
        assert result.recurrence.is_exception is True
        assert result.recurrence.original_start is not None
        assert result.recurrence.original_start.hour == 10

    def test_kairos_tags_open_extensions(self):
        """Should detect Kairos tags from openExtensions."""
        ms_event = {
            "id": "event123",
            "subject": "📞 Kairos Debrief",
            "start": {"dateTime": "2025-01-05T17:30:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T17:45:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "extensions": [
                {
                    "id": "kairos",
                    "type": "kairos.debrief",
                    "user_id": "user123",
                    "date": "2025-01-05",
                }
            ],
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.is_debrief_event is True
        assert result.kairos_tags.get("type") == "debrief"
        assert result.kairos_tags.get("user_id") == "user123"
        assert result.kairos_tags.get("date") == "2025-01-05"

    def test_kairos_tags_single_value_extended_properties(self):
        """Should detect Kairos tags from singleValueExtendedProperties."""
        ms_event = {
            "id": "event123",
            "subject": "📞 Kairos Debrief",
            "start": {"dateTime": "2025-01-05T17:30:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T17:45:00", "timeZone": "UTC"},
            "changeKey": "key123",
            "singleValueExtendedProperties": [
                {
                    "id": "String {00000000-0000-0000-0000-000000000000} Name kairos.type",
                    "value": "debrief",
                },
                {
                    "id": "String {00000000-0000-0000-0000-000000000000} Name kairos.user_id",
                    "value": "user123",
                },
                {
                    "id": "String {00000000-0000-0000-0000-000000000000} Name kairos.date",
                    "value": "2025-01-05",
                },
            ],
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.is_debrief_event is True
        assert result.kairos_tags.get("type") == "debrief"

    def test_missing_optional_fields(self):
        """Should handle events with minimal fields."""
        ms_event = {
            "id": "minimal123",
            "subject": "Minimal Event",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T11:00:00", "timeZone": "UTC"},
            "changeKey": "key123",
            # No description, location, attendees, organizer, etc.
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        assert result.title == "Minimal Event"
        assert result.description is None
        assert result.location is None
        assert len(result.attendees) == 0
        assert result.organizer is None
        assert result.conference is None
        assert result.recurrence is None

    def test_missing_required_fields(self):
        """Should raise error if required fields are missing."""
        ms_event = {
            # Missing id
            "subject": "No ID Event",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T11:00:00", "timeZone": "UTC"},
        }

        with pytest.raises(ValueError, match="Event missing required field: id"):
            normalize_microsoft_event(ms_event, user_id="user123")

    def test_ttl_calculation(self):
        """Should calculate TTL correctly (180 days for normal, 365 for debrief)."""
        ms_event = {
            "id": "event123",
            "subject": "Normal Event",
            "start": {"dateTime": "2025-01-05T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2025-01-05T11:00:00", "timeZone": "UTC"},
            "changeKey": "key123",
        }

        result = normalize_microsoft_event(ms_event, user_id="user123")

        # TTL should be 180 days from end
        expected_ttl = int(result.end.timestamp()) + (180 * 24 * 60 * 60)
        assert result.ttl == expected_ttl

        # Test debrief event (365 days)
        debrief_event = {
            **ms_event,
            "extensions": [{"id": "kairos", "type": "kairos.debrief"}],
        }
        debrief_result = normalize_microsoft_event(debrief_event, user_id="user123")
        expected_debrief_ttl = int(debrief_result.end.timestamp()) + (365 * 24 * 60 * 60)
        assert debrief_result.ttl == expected_debrief_ttl
