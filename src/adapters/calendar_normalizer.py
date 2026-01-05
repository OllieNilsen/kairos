"""Calendar event normalizer - converts provider formats to KCNF.

This module normalizes Google Calendar and Microsoft Graph events into
Kairos Calendar Normal Form (KCNF). All downstream logic uses KCNF to avoid
provider-specific branching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.core.models import (
    AttendeeInfo,
    ConferenceInfo,
    KairosCalendarEvent,
    OrganizerInfo,
    RecurrenceInfo,
)

# Microsoft timezone name mappings to IANA
MS_TIMEZONE_TO_IANA = {
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Prague",
    "Romance Standard Time": "Europe/Paris",
    "Central European Standard Time": "Europe/Warsaw",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "Jordan Standard Time": "Asia/Amman",
    "GTB Standard Time": "Europe/Bucharest",
    "Middle East Standard Time": "Asia/Beirut",
    "Egypt Standard Time": "Africa/Cairo",
    "E. Europe Standard Time": "Europe/Chisinau",
    "Syria Standard Time": "Asia/Damascus",
    "West Bank Standard Time": "Asia/Hebron",
    "South Africa Standard Time": "Africa/Johannesburg",
    "FLE Standard Time": "Europe/Kiev",
    "Israel Standard Time": "Asia/Jerusalem",
    "Kaliningrad Standard Time": "Europe/Kaliningrad",
    "Sudan Standard Time": "Africa/Khartoum",
    "Libya Standard Time": "Africa/Tripoli",
    "Namibia Standard Time": "Africa/Windhoek",
    "Arabic Standard Time": "Asia/Baghdad",
    "Turkey Standard Time": "Europe/Istanbul",
    "Arab Standard Time": "Asia/Riyadh",
    "Belarus Standard Time": "Europe/Minsk",
    "Russian Standard Time": "Europe/Moscow",
    "E. Africa Standard Time": "Africa/Nairobi",
    "Iran Standard Time": "Asia/Tehran",
    "Arabian Standard Time": "Asia/Dubai",
    "Astrakhan Standard Time": "Europe/Astrakhan",
    "Azerbaijan Standard Time": "Asia/Baku",
    "Russia Time Zone 3": "Europe/Samara",
    "Mauritius Standard Time": "Indian/Mauritius",
    "Saratov Standard Time": "Europe/Saratov",
    "Georgian Standard Time": "Asia/Tbilisi",
    "Volgograd Standard Time": "Europe/Volgograd",
    "Caucasus Standard Time": "Asia/Yerevan",
    "Afghanistan Standard Time": "Asia/Kabul",
    "West Asia Standard Time": "Asia/Tashkent",
    "Ekaterinburg Standard Time": "Asia/Yekaterinburg",
    "Pakistan Standard Time": "Asia/Karachi",
    "Qyzylorda Standard Time": "Asia/Qyzylorda",
    "India Standard Time": "Asia/Kolkata",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "Nepal Standard Time": "Asia/Kathmandu",
    "Central Asia Standard Time": "Asia/Almaty",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "Omsk Standard Time": "Asia/Omsk",
    "Myanmar Standard Time": "Asia/Yangon",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Altai Standard Time": "Asia/Barnaul",
    "W. Mongolia Standard Time": "Asia/Hovd",
    "North Asia Standard Time": "Asia/Krasnoyarsk",
    "N. Central Asia Standard Time": "Asia/Novosibirsk",
    "Tomsk Standard Time": "Asia/Tomsk",
    "China Standard Time": "Asia/Shanghai",
    "North Asia East Standard Time": "Asia/Irkutsk",
    "Singapore Standard Time": "Asia/Singapore",
    "W. Australia Standard Time": "Australia/Perth",
    "Taipei Standard Time": "Asia/Taipei",
    "Ulaanbaatar Standard Time": "Asia/Ulaanbaatar",
    "Aus Central W. Standard Time": "Australia/Eucla",
    "Transbaikal Standard Time": "Asia/Chita",
    "Tokyo Standard Time": "Asia/Tokyo",
    "North Korea Standard Time": "Asia/Pyongyang",
    "Korea Standard Time": "Asia/Seoul",
    "Yakutsk Standard Time": "Asia/Yakutsk",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin",
    "E. Australia Standard Time": "Australia/Brisbane",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "West Pacific Standard Time": "Pacific/Port_Moresby",
    "Tasmania Standard Time": "Australia/Hobart",
    "Vladivostok Standard Time": "Asia/Vladivostok",
    "Lord Howe Standard Time": "Australia/Lord_Howe",
    "Bougainville Standard Time": "Pacific/Bougainville",
    "Russia Time Zone 10": "Asia/Srednekolymsk",
    "Magadan Standard Time": "Asia/Magadan",
    "Norfolk Standard Time": "Pacific/Norfolk",
    "Sakhalin Standard Time": "Asia/Sakhalin",
    "Central Pacific Standard Time": "Pacific/Guadalcanal",
    "Russia Time Zone 11": "Asia/Kamchatka",
    "New Zealand Standard Time": "Pacific/Auckland",
    "UTC+12": "Etc/GMT-12",
    "Fiji Standard Time": "Pacific/Fiji",
    "Chatham Islands Standard Time": "Pacific/Chatham",
    "UTC+13": "Etc/GMT-13",
    "Tonga Standard Time": "Pacific/Tongatapu",
    "Samoa Standard Time": "Pacific/Apia",
    "Line Islands Standard Time": "Pacific/Kiritimati",
    "Azores Standard Time": "Atlantic/Azores",
    "Cape Verde Standard Time": "Atlantic/Cape_Verde",
    "Morocco Standard Time": "Africa/Casablanca",
    "Coordinated Universal Time": "UTC",
    "GMT": "Etc/GMT",
    "Coordinated Universal Time-02": "Etc/GMT+2",
    "Greenland Standard Time": "America/Godthab",
    "Montevideo Standard Time": "America/Montevideo",
    "Magallanes Standard Time": "America/Punta_Arenas",
    "Saint Pierre Standard Time": "America/Miquelon",
    "Bahia Standard Time": "America/Bahia",
    "UTC-02": "Etc/GMT+2",
    "Tocantins Standard Time": "America/Araguaina",
    "E. South America Standard Time": "America/Sao_Paulo",
    "SA Eastern Standard Time": "America/Cayenne",
    "Argentina Standard Time": "America/Buenos_Aires",
    "Newfoundland Standard Time": "America/St_Johns",
    "Paraguay Standard Time": "America/Asuncion",
    "Atlantic Standard Time": "America/Halifax",
    "Central Brazilian Standard Time": "America/Cuiaba",
    "SA Western Standard Time": "America/La_Paz",
    "Pacific SA Standard Time": "America/Santiago",
    "Turks And Caicos Standard Time": "America/Grand_Turk",
    "Venezuela Standard Time": "America/Caracas",
    "Eastern Standard Time": "America/New_York",
    "Haiti Standard Time": "America/Port-au-Prince",
    "Cuba Standard Time": "America/Havana",
    "US Eastern Standard Time": "America/Indianapolis",
    "Central America Standard Time": "America/Guatemala",
    "Central Standard Time": "America/Chicago",
    "Easter Island Standard Time": "Pacific/Easter",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina",
    "SA Pacific Standard Time": "America/Bogota",
    "Mountain Standard Time (Mexico)": "America/Chihuahua",
    "Mountain Standard Time": "America/Denver",
    "US Mountain Standard Time": "America/Phoenix",
    "Yukon Standard Time": "America/Whitehorse",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Aleutian Standard Time": "America/Adak",
    "Marquesas Standard Time": "Pacific/Marquesas",
    "UTC-11": "Etc/GMT+11",
    "Dateline Standard Time": "Etc/GMT+12",
}


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


# ============================================================================
# Microsoft Graph Event Normalizer
# ============================================================================


def _parse_microsoft_datetime(dt_obj: dict[str, Any]) -> datetime:
    """Parse Microsoft Graph dateTime object to tz-aware datetime.

    Microsoft sends:
    - {"dateTime": "2025-01-05T14:00:00.0000000", "timeZone": "Eastern Standard Time"}

    Args:
        dt_obj: Microsoft Graph dateTime object

    Returns:
        Timezone-aware datetime object in UTC
    """
    dt_str = dt_obj.get("dateTime")
    tz_name = dt_obj.get("timeZone", "UTC")

    if not dt_str:
        raise ValueError(f"Invalid Microsoft dateTime object: {dt_obj}")

    # Parse datetime string (remove microseconds if present)
    if "." in dt_str:
        dt_str = dt_str.split(".")[0]

    dt_naive = datetime.fromisoformat(dt_str)

    # Convert Microsoft timezone name to IANA timezone
    iana_tz_name = MS_TIMEZONE_TO_IANA.get(tz_name, "UTC")

    try:
        tz = ZoneInfo(iana_tz_name)
        dt_aware = dt_naive.replace(tzinfo=tz)
        # Convert to UTC for storage
        return dt_aware.astimezone(UTC)
    except Exception:
        # Fallback: assume UTC if timezone conversion fails
        return dt_naive.replace(tzinfo=UTC)


def _extract_microsoft_attendees(event: dict[str, Any]) -> list[AttendeeInfo]:
    """Extract attendees from Microsoft Graph event.

    Args:
        event: Microsoft Graph event dict

    Returns:
        List of AttendeeInfo objects (max 200 for item size protection)
    """
    attendees = event.get("attendees", [])
    result = []

    for attendee in attendees[:200]:  # Cap at 200 (item size guard)
        email_addr = attendee.get("emailAddress", {})
        email = email_addr.get("address")
        name = email_addr.get("name")

        # Fallback: use email prefix if no name
        if not name and email:
            name = email.split("@")[0]

        if email:
            result.append(AttendeeInfo(name=name, email=email))

    return result


def _extract_microsoft_organizer(event: dict[str, Any]) -> OrganizerInfo | None:
    """Extract organizer from Microsoft Graph event.

    Args:
        event: Microsoft Graph event dict

    Returns:
        OrganizerInfo or None if organizer not present
    """
    organizer = event.get("organizer")
    if not organizer:
        return None

    email_addr = organizer.get("emailAddress", {})
    email = email_addr.get("address")
    name = email_addr.get("name")

    # Fallback: use email prefix if no name
    if not name and email:
        name = email.split("@")[0]

    return OrganizerInfo(name=name, email=email)


def _extract_microsoft_conference(event: dict[str, Any]) -> ConferenceInfo | None:
    """Extract conference/video call info from Microsoft Graph event.

    Args:
        event: Microsoft Graph event dict

    Returns:
        ConferenceInfo or None if no conference data
    """
    online_meeting = event.get("onlineMeeting")
    if not online_meeting:
        return None

    join_url = online_meeting.get("joinUrl")
    conference_id = online_meeting.get("conferenceId")

    if not join_url:
        return None

    return ConferenceInfo(join_url=join_url, conference_id=conference_id)


def _extract_microsoft_recurrence(event: dict[str, Any]) -> RecurrenceInfo | None:
    """Extract recurrence metadata from Microsoft Graph event.

    Microsoft Graph recurrence fields:
    - type: seriesMaster, occurrence, exception, singleInstance
    - seriesMasterId: Series master ID (present on instances/exceptions)
    - recurrence: Recurrence pattern (present on series masters)
    - originalStart: Original start for exceptions (moved instances)

    Args:
        event: Microsoft Graph event dict

    Returns:
        RecurrenceInfo or None if not a recurring event
    """
    event_type = event.get("type")
    series_master_id = event.get("seriesMasterId")
    original_start_str = event.get("originalStart")
    original_start_tz = event.get("originalStartTimeZone")

    # Not a recurring event if singleInstance or no type field
    if not event_type or event_type == "singleInstance":
        return None

    # Only create RecurrenceInfo if it's actually a recurring event
    if event_type not in ("seriesMaster", "occurrence", "exception"):
        return None

    # Parse original start time if present (for exceptions)
    original_start_dt = None
    if original_start_str and original_start_tz:
        try:
            original_start_dt = _parse_microsoft_datetime(
                {"dateTime": original_start_str, "timeZone": original_start_tz}
            )
        except Exception:
            original_start_dt = None

    # Determine if this is an exception (moved/modified instance)
    is_exception = event_type == "exception"

    # Determine if this is a recurring instance
    is_recurring_instance = event_type in ("occurrence", "exception")

    # Extract provider series ID and instance ID
    provider_series_id = series_master_id if is_recurring_instance else event.get("id")
    provider_instance_id = event.get("id") if is_recurring_instance else None

    # For series masters, there's no series_master_id (they ARE the master)
    if event_type == "seriesMaster":
        provider_series_id = event.get("id")

    return RecurrenceInfo(
        provider_series_id=provider_series_id,
        provider_instance_id=provider_instance_id,
        is_recurring_instance=is_recurring_instance,
        is_exception=is_exception,
        original_start=original_start_dt,
        recurrence_rule=None,  # Microsoft sends structured pattern, not RRULE string
    )


def _extract_microsoft_kairos_tags(event: dict[str, Any]) -> dict[str, Any]:
    """Extract Kairos tags from Microsoft Graph event.

    Microsoft can store custom properties in:
    - extensions[] (openExtensions)
    - singleValueExtendedProperties[]

    Args:
        event: Microsoft Graph event dict

    Returns:
        Dictionary of Kairos tags
    """
    tags = {}

    # Check openExtensions
    extensions = event.get("extensions", [])
    for ext in extensions:
        ext_id = ext.get("id", "")
        ext_type = ext.get("type", "")

        if ext_id == "kairos" or ext_id.startswith("kairos."):
            # Special handling for "type" field in extension
            # If extension has "type": "kairos.debrief", extract "debrief" as type value
            if ext_type and ext_type.startswith("kairos."):
                type_value = ext_type.replace("kairos.", "")
                tags["type"] = type_value

            # Extract all other fields from this extension
            for key, value in ext.items():
                if key not in ("id", "@odata.type", "type"):
                    # Remove kairos. prefix if present
                    tag_name = key.replace("kairos.", "").replace("kairos_", "")
                    tags[tag_name] = value

    # Check singleValueExtendedProperties
    extended_props = event.get("singleValueExtendedProperties", [])
    for prop in extended_props:
        prop_id = prop.get("id", "")
        # Extract property name from GUID-based ID
        # Format: "String {GUID} Name kairos.property_name"
        if (" Name " in prop_id) and ("kairos." in prop_id or "kairos_" in prop_id):
            name_part = prop_id.split(" Name ")[-1]
            tag_name = name_part.replace("kairos.", "").replace("kairos_", "")
            tags[tag_name] = prop.get("value")

    return tags


def normalize_microsoft_event(
    event: dict[str, Any],
    user_id: str,
    ingested_at: datetime | None = None,
) -> KairosCalendarEvent:
    """Normalize a Microsoft Graph event to KCNF.

    Args:
        event: Microsoft Graph event dict from API
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
        start = _parse_microsoft_datetime(start_obj)
        end = _parse_microsoft_datetime(end_obj)
    except (ValueError, KeyError) as e:
        raise ValueError(f"Invalid start/end time in event {event_id}: {e}") from e

    # Detect all-day events
    is_all_day = event.get("isAllDay", False)

    # Extract fields
    title = event.get("subject")
    description = _truncate_description(event.get("bodyPreview"))
    location_obj = event.get("location", {})
    location = location_obj.get("displayName") if isinstance(location_obj, dict) else None

    # Map Microsoft status to KCNF format
    # Microsoft uses responseStatus.response, not a top-level status field
    # For KCNF, we use "confirmed" as default (Microsoft doesn't have event-level status like Google)
    status = "confirmed"

    # People
    attendees = _extract_microsoft_attendees(event)
    organizer = _extract_microsoft_organizer(event)

    # Conference
    conference = _extract_microsoft_conference(event)

    # Recurrence
    recurrence = _extract_microsoft_recurrence(event)

    # Kairos metadata
    kairos_tags = _extract_microsoft_kairos_tags(event)
    is_debrief_event = kairos_tags.get("type") == "debrief"

    # Provider version (use changeKey as version guard)
    change_key = event.get("changeKey")
    provider_version = change_key or event.get("lastModifiedDateTime", ingested_at.isoformat())

    # Last modified (from Microsoft)
    last_modified_str = event.get("lastModifiedDateTime")
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
        provider="microsoft",
        provider_calendar_id=organizer.email if organizer else None,
        provider_event_id=event_id,
        provider_etag=None,  # Google only
        provider_change_key=change_key,
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
