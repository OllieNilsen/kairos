"""Google Calendar API adapter."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

# Google API endpoints
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarClient:
    """Client for Google Calendar API using OAuth2."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token: str | None = None
        self._token_expiry: datetime | None = None

    @classmethod
    def from_ssm(cls) -> GoogleCalendarClient:
        """Create client using credentials from SSM Parameter Store."""
        # Import here to avoid module-level dependency on SSM (for testing)
        from adapters.ssm import get_parameter

        return cls(
            client_id=get_parameter("/kairos/google-client-id", decrypt=False),
            client_secret=get_parameter("/kairos/google-client-secret"),
            refresh_token=get_parameter("/kairos/google-refresh-token"),
        )

    def _get_access_token(self) -> str:
        """Get a valid access token, refreshing if necessary."""
        now = datetime.now()

        # Return cached token if still valid (with 5 min buffer)
        if (
            self._access_token
            and self._token_expiry
            and now < self._token_expiry - timedelta(minutes=5)
        ):
            return self._access_token

        # Refresh the access token
        response = httpx.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        data = response.json()

        self._access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self._token_expiry = now + timedelta(seconds=expires_in)

        return self._access_token

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        """Make an authenticated request to the Calendar API."""
        token = self._get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        url = f"{CALENDAR_API_BASE}{endpoint}"
        response = httpx.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()

        return response.json()

    def list_events(
        self,
        calendar_id: str = "primary",
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        max_results: int = 50,
        single_events: bool = True,
    ) -> list[dict[str, Any]]:
        """List calendar events within a time range.

        Args:
            calendar_id: Calendar ID (default: primary calendar)
            time_min: Start of time range (default: now)
            time_max: End of time range (default: 24 hours from now)
            max_results: Maximum number of events to return
            single_events: Expand recurring events into instances

        Returns:
            List of event dictionaries
        """
        if time_min is None:
            time_min = datetime.now()
        if time_max is None:
            time_max = time_min + timedelta(days=1)

        params = {
            "timeMin": time_min.isoformat() + "Z",
            "timeMax": time_max.isoformat() + "Z",
            "maxResults": max_results,
            "singleEvents": str(single_events).lower(),
            "orderBy": "startTime",
        }

        data = self._request("GET", f"/calendars/{calendar_id}/events", params=params)
        return data.get("items", [])

    def get_event(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
        """Get a single calendar event by ID.

        Args:
            event_id: The event ID
            calendar_id: Calendar ID (default: primary calendar)

        Returns:
            Event dictionary
        """
        return self._request("GET", f"/calendars/{calendar_id}/events/{event_id}")

    def watch_calendar(
        self,
        webhook_url: str,
        channel_id: str,
        calendar_id: str = "primary",
        ttl_seconds: int = 604800,  # 7 days (max allowed)
    ) -> dict[str, Any]:
        """Set up push notifications for calendar changes.

        Args:
            webhook_url: URL to receive push notifications
            channel_id: Unique identifier for this watch channel
            calendar_id: Calendar ID to watch
            ttl_seconds: Time-to-live for the watch (max 7 days)

        Returns:
            Watch response with resourceId and expiration
        """
        # Calculate expiration time
        expiration = int((datetime.now().timestamp() + ttl_seconds) * 1000)

        body = {
            "id": channel_id,
            "type": "web_hook",
            "address": webhook_url,
            "expiration": expiration,
        }

        return self._request("POST", f"/calendars/{calendar_id}/events/watch", json=body)

    def stop_watch(self, channel_id: str, resource_id: str) -> None:
        """Stop receiving push notifications for a channel.

        Args:
            channel_id: The channel ID from watch_calendar
            resource_id: The resource ID from watch_calendar response
        """
        body = {
            "id": channel_id,
            "resourceId": resource_id,
        }

        token = self._get_access_token()
        response = httpx.post(
            f"{CALENDAR_API_BASE}/channels/stop",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        response.raise_for_status()


def parse_event_datetime(event: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    """Parse start and end times from a Google Calendar event.

    Handles both dateTime (specific time) and date (all-day) formats.

    Returns:
        Tuple of (start_datetime, end_datetime), or None for all-day events
    """
    start_data = event.get("start", {})
    end_data = event.get("end", {})

    start_dt = None
    end_dt = None

    # dateTime is for specific times, date is for all-day events
    if "dateTime" in start_data:
        start_dt = datetime.fromisoformat(start_data["dateTime"].replace("Z", "+00:00"))
    if "dateTime" in end_data:
        end_dt = datetime.fromisoformat(end_data["dateTime"].replace("Z", "+00:00"))

    return start_dt, end_dt


def extract_attendee_names(event: dict[str, Any]) -> list[str]:
    """Extract attendee display names from a calendar event.

    Args:
        event: Google Calendar event dictionary

    Returns:
        List of attendee names (excluding the calendar owner)
    """
    attendees = event.get("attendees", [])
    names = []

    for attendee in attendees:
        # Skip the calendar owner (self)
        if attendee.get("self"):
            continue

        # Prefer displayName, fall back to email
        name = attendee.get("displayName") or attendee.get("email", "Unknown")
        names.append(name)

    return names
