"""Lambda handler for Google Calendar push notifications."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from aws_lambda_powertools import Logger

# Support both Lambda (adapters...) and test (src.adapters...) import paths
try:
    from adapters.google_calendar import (
        GoogleCalendarClient,
        extract_attendees,
        parse_event_datetime,
    )
    from adapters.meetings_repo import MeetingsRepository
    from adapters.scheduler import SchedulerClient, make_prompt_schedule_name
    from adapters.user_state import UserStateRepository
    from core.models import Meeting
except ImportError:
    from src.adapters.google_calendar import (
        GoogleCalendarClient,
        extract_attendees,
        parse_event_datetime,
    )
    from src.adapters.meetings_repo import MeetingsRepository
    from src.adapters.scheduler import SchedulerClient, make_prompt_schedule_name
    from src.adapters.user_state import UserStateRepository
    from src.core.models import Meeting

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="kairos-calendar-webhook")

# Lazy initialization
_calendar_client: GoogleCalendarClient | None = None
_meetings_repo: MeetingsRepository | None = None
_user_state_repo: UserStateRepository | None = None
_scheduler: SchedulerClient | None = None


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


def get_user_state_repo() -> UserStateRepository | None:
    """Get or create the user state repository."""
    global _user_state_repo
    table_name = os.environ.get("USER_STATE_TABLE")
    if not table_name:
        return None
    if _user_state_repo is None:
        _user_state_repo = UserStateRepository(table_name)
    return _user_state_repo


def get_scheduler() -> SchedulerClient:
    """Get or create the scheduler client."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerClient()
    return _scheduler


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

        # Extract description and location
        description = event.get("description", "").strip() or None
        location = event.get("location", "").strip() or None

        # Create or update the meeting
        meeting = Meeting(
            user_id=user_id,
            meeting_id=event["id"],
            title=title,
            description=description,
            location=location,
            start_time=start_dt,
            end_time=end_dt,
            attendees=extract_attendees(event),
            status=existing.status if existing else "pending",
            google_etag=event.get("etag"),
        )

        repo.save_meeting(meeting)
        synced += 1

    logger.info(
        "Calendar sync complete",
        extra={"synced": synced, "skipped": skipped, "total_events": len(google_events)},
    )

    # Also check for debrief event changes
    debrief_result = check_debrief_event_changes(user_id, calendar)

    return {"synced": synced, "skipped": skipped, **debrief_result}


def check_debrief_event_changes(user_id: str, calendar: GoogleCalendarClient) -> dict[str, Any]:
    """Check if today's debrief event was moved or deleted.

    If the user moved the debrief event, we update the schedule.
    If the user deleted it, we cancel today's prompt.

    Args:
        user_id: The user identifier
        calendar: Google Calendar client

    Returns:
        Dict with debrief_action taken (none, moved, deleted)
    """
    user_repo = get_user_state_repo()
    if not user_repo:
        logger.info("User state table not configured - skipping debrief check")
        return {"debrief_action": "skipped"}

    # Get user state to find the debrief event ID
    user_state = user_repo.get_user_state(user_id)
    if not user_state:
        logger.info("User not found - skipping debrief check", extra={"user_id": user_id})
        return {"debrief_action": "skipped"}

    if not user_state.debrief_event_id:
        logger.info("No debrief event configured - skipping")
        return {"debrief_action": "none"}

    # Try to fetch the debrief event from Google Calendar
    try:
        event = calendar.get_event(user_state.debrief_event_id)
    except Exception as e:
        # Event might have been deleted
        logger.info(
            "Could not fetch debrief event - may be deleted",
            extra={"event_id": user_state.debrief_event_id, "error": str(e)},
        )
        return _handle_debrief_deleted(user_id, user_state, user_repo)

    # Check if event was cancelled/deleted
    if event.get("status") == "cancelled":
        logger.info("Debrief event was cancelled")
        return _handle_debrief_deleted(user_id, user_state, user_repo)

    # Check if event was moved (start time changed)
    start_dt, _ = parse_event_datetime(event)
    if start_dt is None:
        logger.warning("Debrief event has no start time - treating as deleted")
        return _handle_debrief_deleted(user_id, user_state, user_repo)

    # Compare with stored next_prompt_at
    if user_state.next_prompt_at:
        stored_time = datetime.fromisoformat(user_state.next_prompt_at.replace("Z", "+00:00"))
        # Normalize to UTC for comparison
        new_time_utc = start_dt.astimezone(UTC)

        # Check if time has changed (more than 1 minute difference)
        time_diff = abs((new_time_utc - stored_time).total_seconds())
        if time_diff > 60:
            logger.info(
                "Debrief event was moved",
                extra={
                    "old_time": stored_time.isoformat(),
                    "new_time": new_time_utc.isoformat(),
                    "diff_seconds": time_diff,
                },
            )
            return _handle_debrief_moved(user_id, user_state, user_repo, new_time_utc, event)

    # Check if etag changed (event was modified but time didn't change)
    if event.get("etag") != user_state.debrief_event_etag:
        logger.info("Debrief event modified but time unchanged - updating etag")
        user_repo.update_debrief_event(
            user_id=user_id,
            debrief_event_id=event["id"],
            debrief_event_etag=event.get("etag"),
        )

    return {"debrief_action": "none"}


def _handle_debrief_deleted(
    user_id: str,
    user_state: Any,
    user_repo: UserStateRepository,
) -> dict[str, str]:
    """Handle when the user deletes the debrief event.

    Clears debrief state and cancels the prompt schedule.
    """
    logger.info("Handling debrief event deletion", extra={"user_id": user_id})

    # Delete the prompt schedule if it exists
    if user_state.prompt_schedule_name:
        scheduler = get_scheduler()
        scheduler.delete_schedule(user_state.prompt_schedule_name)
        logger.info(
            "Deleted prompt schedule",
            extra={"schedule_name": user_state.prompt_schedule_name},
        )

    # Clear debrief fields in user state
    user_repo.clear_debrief_event(user_id)
    logger.info("Cleared debrief event from user state")

    return {"debrief_action": "deleted"}


def _handle_debrief_moved(
    user_id: str,
    user_state: Any,
    user_repo: UserStateRepository,
    new_time_utc: datetime,
    event: dict[str, Any],
) -> dict[str, str]:
    """Handle when the user moves the debrief event to a new time.

    Updates the prompt schedule to fire at the new time.
    """
    logger.info(
        "Handling debrief event move",
        extra={"user_id": user_id, "new_time": new_time_utc.isoformat()},
    )

    # Check if new time is in the past
    now = datetime.now(UTC)
    if new_time_utc <= now:
        logger.info("New debrief time is in the past - deleting schedule")
        if user_state.prompt_schedule_name:
            scheduler = get_scheduler()
            scheduler.delete_schedule(user_state.prompt_schedule_name)
        user_repo.clear_debrief_event(user_id)
        return {"debrief_action": "deleted_past"}

    # Get today's date for schedule naming
    user_tz = ZoneInfo(user_state.timezone or "Europe/London")
    today_str = datetime.now(user_tz).strftime("%Y-%m-%d")
    schedule_name = make_prompt_schedule_name(user_id, today_str)

    # Get scheduler config
    prompt_sender_fn_name = os.environ.get("PROMPT_SENDER_FUNCTION_NAME", "kairos-prompt-sender")
    scheduler_role_arn = os.environ.get("SCHEDULER_ROLE_ARN", "")
    region = os.environ.get("AWS_REGION", "eu-west-1")

    if not scheduler_role_arn:
        logger.warning("SCHEDULER_ROLE_ARN not configured - cannot reschedule")
        return {"debrief_action": "reschedule_failed"}

    # Construct prompt sender ARN
    account_id = _get_account_id()
    prompt_sender_arn = f"arn:aws:lambda:{region}:{account_id}:function:{prompt_sender_fn_name}"

    # Update the schedule
    scheduler = get_scheduler()
    scheduler.upsert_one_time_schedule(
        name=schedule_name,
        at_time_utc_iso=new_time_utc.isoformat().replace("+00:00", "Z"),
        target_arn=prompt_sender_arn,
        payload={"user_id": user_id, "date": today_str},
        role_arn=scheduler_role_arn,
        description=f"Kairos debrief prompt for {user_id} (rescheduled)",
    )
    logger.info("Rescheduled prompt", extra={"schedule_name": schedule_name})

    # Update user state with new time and etag
    user_repo.update_prompt_schedule(
        user_id=user_id,
        next_prompt_at=new_time_utc.isoformat(),
        prompt_schedule_name=schedule_name,
    )

    # Update etag
    user_repo.update_debrief_event(
        user_id=user_id,
        debrief_event_id=event["id"],
        debrief_event_etag=event.get("etag"),
    )

    return {"debrief_action": "moved"}


# Cached account ID
_account_id: str | None = None


def _get_account_id() -> str:
    """Get AWS account ID from STS (cached)."""
    global _account_id
    if _account_id is None:
        import boto3

        sts = boto3.client("sts")
        _account_id = sts.get_caller_identity()["Account"]
    return _account_id
