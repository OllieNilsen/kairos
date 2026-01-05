"""Calendar event normalizer - converts provider formats to KCNF.

This module normalizes Google Calendar and Microsoft Graph events into
Kairos Calendar Normal Form (KCNF). All downstream logic uses KCNF to avoid
provider-specific branching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.core.models import (
    AttendeeInfo,
    ConferenceInfo,
    KairosCalendarEvent,
    OrganizerInfo,
    RecurrenceInfo,
)


def _parse_google_datetime(dt_obj: dict[str, Any]) -> datetime:
    """Parse Google Calendar dateTime or date object to tz-aware datetime.

    Google sends either:
    - {"dateTime": "2025-01-05T14:00:00-05:00", "timeZone": "America/New_York"}
    - {"date": "2025-01-05"}  (all-day events)

    Args:
        dt_obj: Google Calendar dateTime or date object

    Returns:
        Timezone-aware datetime object (all-day events use midnight UTC)
    """
    if "dateTime" in dt_obj:
        # Parse ISO timestamp (includes timezone)
        dt_str = dt_obj["dateTime"]
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    elif "date" in dt_obj:
        # All-day event - use midnight UTC
        date_str = dt_obj["date"]
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        raise ValueError(f"Invalid Google dateTime object: {dt_obj}")


def _extract_google_attendees(event: dict[str, Any]) -> list[AttendeeInfo]:
    """Extract attendees from Google Calendar event.

    Args:
        event: Google Calendar event dict

    Returns:
        List of AttendeeInfo objects (max 200 for item size protection)
    """
    attendees = event.get("attendees", [])
    result = []
    for attendee in attendees[:200]:  # Cap at 200 (item size guard)
        email = attendee.get("email")
        display_name = attendee.get("displayName") or (email.split("@")[0] if email else "Unknown")
        if email:
            result.append(AttendeeInfo(name=display_name, email=email))
    return result


def _extract_google_organizer(event: dict[str, Any]) -> OrganizerInfo | None:
    """Extract organizer from Google Calendar event.

    Args:
        event: Google Calendar event dict

    Returns:
        OrganizerInfo or None if organizer not present
    """
    organizer = event.get("organizer")
    if not organizer:
        return None

    email = organizer.get("email")
    display_name = organizer.get("displayName") or (email.split("@")[0] if email else None)
    return OrganizerInfo(name=display_name, email=email)


def _extract_google_conference(event: dict[str, Any]) -> ConferenceInfo | None:
    """Extract conference/video call info from Google Calendar event.

    Args:
        event: Google Calendar event dict

    Returns:
        ConferenceInfo or None if no conference data
    """
    conference_data = event.get("conferenceData")
    if not conference_data:
        # Fallback: check hangoutLink (legacy Google Meet)
        hangout_link = event.get("hangoutLink")
        if hangout_link:
            return ConferenceInfo(join_url=hangout_link)
        return None

    # Extract join URL from entryPoints
    entry_points = conference_data.get("entryPoints", [])
    join_url = None
    phone = None

    for entry in entry_points:
        if entry.get("entryPointType") == "video" and not join_url:
            join_url = entry.get("uri")
        elif entry.get("entryPointType") == "phone" and not phone:
            phone = entry.get("uri")

    if not join_url and not phone:
        return None

    conference_id = conference_data.get("conferenceId")
    return ConferenceInfo(join_url=join_url, conference_id=conference_id, phone=phone)


def _extract_google_recurrence(event: dict[str, Any]) -> RecurrenceInfo | None:
    """Extract recurrence metadata from Google Calendar event.

    Google Calendar recurrence fields:
    - recurringEventId: Series master ID (present on instances)
    - recurrence: RRULE array (present on series masters)
    - originalStartTime: Original start for exceptions (moved instances)

    Args:
        event: Google Calendar event dict

    Returns:
        RecurrenceInfo or None if not a recurring event
    """
    recurring_event_id = event.get("recurringEventId")
    recurrence_rules = event.get("recurrence")
    original_start = event.get("originalStartTime")

    # Not a recurring event if none of these are present
    if not recurring_event_id and not recurrence_rules and not original_start:
        return None

    # Parse original start time if present (for exceptions)
    original_start_dt = None
    if original_start:
        original_start_dt = _parse_google_datetime(original_start)

    # Determine if this is an exception (moved/modified instance)
    is_exception = original_start is not None

    # Extract RRULE (only present on series masters)
    recurrence_rule = None
    if recurrence_rules:
        # Google sends array of RRULE strings, take first one
        recurrence_rule = recurrence_rules[0] if recurrence_rules else None

    return RecurrenceInfo(
        provider_series_id=recurring_event_id,  # Series master ID
        provider_instance_id=event.get("id"),  # This instance's ID
        is_recurring_instance=recurring_event_id is not None,
        is_exception=is_exception,
        original_start=original_start_dt,
        recurrence_rule=recurrence_rule,
    )


def _extract_kairos_tags(event: dict[str, Any]) -> dict[str, Any]:
    """Extract Kairos tags from Google Calendar extended properties.

    Google stores custom properties in extendedProperties.private.

    Args:
        event: Google Calendar event dict

    Returns:
        Dictionary of Kairos tags
    """
    extended_props = event.get("extendedProperties", {})
    private_props = extended_props.get("private", {})

    tags = {}
    for key, value in private_props.items():
        if key.startswith("kairos_"):
            # Remove kairos_ prefix
            tag_name = key[7:]
            tags[tag_name] = value

    return tags


def _truncate_description(description: str | None) -> str | None:
    """Truncate description to 8KB max (item size guard).

    Args:
        description: Event description

    Returns:
        Truncated description or None
    """
    if not description:
        return None

    max_bytes = 8 * 1024  # 8KB
    if len(description.encode("utf-8")) <= max_bytes:
        return description

    # Truncate to fit in 8KB (leave room for "...")
    truncated = description.encode("utf-8")[: max_bytes - 10].decode("utf-8", errors="ignore")
    return truncated + "..."


def normalize_google_event(
    event: dict[str, Any],
    user_id: str,
    ingested_at: datetime | None = None,
) -> KairosCalendarEvent:
    """Normalize a Google Calendar event to KCNF.

    Args:
        event: Google Calendar event dict from API
        user_id: User ID for partitioning
        ingested_at: Ingestion timestamp (defaults to now)

    Returns:
        KairosCalendarEvent in KCNF format

    Raises:
        ValueError: If required fields are missing
    """
    if ingested_at is None:
        ingested_at = datetime.now(UTC)

    # Required fields
    event_id = event.get("id")
    if not event_id:
        raise ValueError("Event missing required field: id")

    # Parse start/end times (MUST be tz-aware)
    start_obj = event.get("start", {})
    end_obj = event.get("end", {})

    try:
        start = _parse_google_datetime(start_obj)
        end = _parse_google_datetime(end_obj)
    except (ValueError, KeyError) as e:
        raise ValueError(f"Invalid start/end time in event {event_id}: {e}") from e

    # Detect all-day events
    is_all_day = "date" in start_obj

    # Extract fields
    title = event.get("summary")
    description = _truncate_description(event.get("description"))
    location = event.get("location")
    status = event.get("status")  # confirmed, cancelled, tentative

    # People
    attendees = _extract_google_attendees(event)
    organizer = _extract_google_organizer(event)

    # Conference
    conference = _extract_google_conference(event)

    # Recurrence
    recurrence = _extract_google_recurrence(event)

    # Kairos metadata
    kairos_tags = _extract_kairos_tags(event)
    is_debrief_event = kairos_tags.get("type") == "debrief"

    # Provider version (use etag as version guard)
    etag = event.get("etag")
    provider_version = etag or event.get("updated", ingested_at.isoformat())

    # Last modified (from Google)
    last_modified_str = event.get("updated")
    last_modified = None
    if last_modified_str:
        last_modified = datetime.fromisoformat(last_modified_str.replace("Z", "+00:00"))

    # TTL: 180 days from end (for auto-cleanup)
    # Exception: debrief events get 365 days
    ttl_days = 365 if is_debrief_event else 180
    ttl_timestamp = int(end.timestamp()) + (ttl_days * 24 * 60 * 60)

    return KairosCalendarEvent(
        # Tenant + provider identity
        user_id=user_id,
        provider="google",
        provider_calendar_id=event.get("organizer", {}).get("email"),
        provider_event_id=event_id,
        provider_etag=etag,
        provider_change_key=None,  # Microsoft only
        provider_version=provider_version,
        # Core fields
        title=title,
        description=description,
        location=location,
        start=start,
        end=end,
        is_all_day=is_all_day,
        status=status,
        # People
        organizer=organizer,
        attendees=attendees,
        # Conference
        conference=conference,
        # Recurrence
        recurrence=recurrence,
        # Kairos metadata
        is_debrief_event=is_debrief_event,
        kairos_tags=kairos_tags,
        # Sync/audit
        ingested_at=ingested_at,
        last_modified_at=last_modified,
        # DynamoDB fields
        item_type="event",
        redirect_to_sk=None,
        ttl=ttl_timestamp,
    )
