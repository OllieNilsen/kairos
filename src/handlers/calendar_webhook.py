"""Lambda handler for Google Calendar push notifications."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from aws_lambda_powertools import Logger

from adapters.google_calendar import (
    GoogleCalendarClient,
    extract_attendee_names,
    parse_event_datetime,
)
from adapters.meetings_repo import MeetingsRepository
from core.models import Meeting

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="kairos-calendar-webhook")

# Lazy initialization
_calendar_client: GoogleCalendarClient | None = None
_meetings_repo: MeetingsRepository | None = None


def get_calendar_client() -> GoogleCalendarClient:
    """Get or create the Google Calendar client."""
    global _calendar_client
    if _calendar_client is None:
        _calendar_client = GoogleCalendarClient.from_ssm()
    return _calendar_client


def get_meetings_repo() -> MeetingsRepository:
    """Get or create the meetings repository."""
    global _meetings_repo
    if _meetings_repo is None:
        table_name = os.environ["MEETINGS_TABLE_NAME"]
        _meetings_repo = MeetingsRepository(table_name)
    return _meetings_repo


@logger.inject_lambda_context
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Handle Google Calendar push notifications.

    Google sends two types of notifications:
    1. sync - Initial sync request (we should do a full sync)
    2. exists - Resource changed (incremental update)

    Headers contain:
    - X-Goog-Channel-ID: Our channel ID
    - X-Goog-Resource-State: sync | exists | not_exists
    - X-Goog-Resource-ID: The resource being watched
    """
    headers = event.get("headers", {})

    # Normalize header keys to lowercase
    headers = {k.lower(): v for k, v in headers.items()}

    resource_state = headers.get("x-goog-resource-state", "")
    channel_id = headers.get("x-goog-channel-id", "")

    logger.info(
        "Received calendar notification",
        extra={"resource_state": resource_state, "channel_id": channel_id},
    )

    # Google sends a sync notification when watch is first set up
    if resource_state == "sync":
        logger.info("Sync notification - doing full calendar sync")
        sync_result = sync_calendar_events()
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "synced", **sync_result}),
        }

    # exists means the calendar has changes
    if resource_state == "exists":
        logger.info("Calendar changed - syncing events")
        sync_result = sync_calendar_events()
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "synced", **sync_result}),
        }

    # not_exists means the resource was deleted (rare)
    if resource_state == "not_exists":
        logger.warning("Resource deleted notification")
        return {"statusCode": 200, "body": json.dumps({"status": "ignored"})}

    # Unknown state - still return 200 to acknowledge
    logger.warning("Unknown resource state", extra={"resource_state": resource_state})
    return {"statusCode": 200, "body": json.dumps({"status": "ignored"})}


def sync_calendar_events() -> dict[str, int]:
    """Sync calendar events from Google Calendar to DynamoDB.

    Fetches events for today and tomorrow, updates DynamoDB accordingly.

    Returns:
        Dict with counts of synced, updated, deleted events
    """
    # MVP: hardcoded user ID (will be dynamic in multi-user version)
    user_id = os.environ.get("USER_ID", "default")

    calendar = get_calendar_client()
    repo = get_meetings_repo()

    # Fetch events for today and tomorrow
    now = datetime.now()
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_tomorrow = start_of_today + timedelta(days=2)

    google_events = calendar.list_events(
        time_min=start_of_today,
        time_max=end_of_tomorrow,
        max_results=100,
    )

    synced = 0
    skipped = 0

    for event in google_events:
        # Skip all-day events (no specific time)
        start_dt, end_dt = parse_event_datetime(event)
        if start_dt is None or end_dt is None:
            skipped += 1
            continue

        # Skip events without a summary (untitled)
        title = event.get("summary", "").strip()
        if not title:
            title = "(No title)"

        # Skip cancelled events
        if event.get("status") == "cancelled":
            # Delete from DynamoDB if it exists
            repo.delete_meeting(user_id, event["id"])
            continue

        # Check if we already have this meeting
        existing = repo.get_meeting(user_id, event["id"])

        # Skip if etag hasn't changed (no update needed)
        if existing and existing.google_etag == event.get("etag"):
            continue

        # Create or update the meeting
        meeting = Meeting(
            user_id=user_id,
            meeting_id=event["id"],
            title=title,
            start_time=start_dt,
            end_time=end_dt,
            attendees=extract_attendee_names(event),
            status=existing.status if existing else "pending",
            google_etag=event.get("etag"),
        )

        repo.save_meeting(meeting)
        synced += 1

    logger.info(
        "Calendar sync complete",
        extra={"synced": synced, "skipped": skipped, "total_events": len(google_events)},
    )

    return {"synced": synced, "skipped": skipped}
