"""Daily planning Lambda handler.

Runs at 08:00 Europe/London each day to:
1. Acquire daily lease (prevent duplicate runs)
2. Reset daily counters
3. Compute today's debrief time from preferred_prompt_time
4. Create/update the Google Calendar debrief event
5. Schedule one-time EventBridge trigger for prompt sender
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aws_lambda_powertools import Logger

# Support both Lambda (adapters.*) and test (src.adapters.*) import paths
try:
    from adapters.google_calendar import GoogleCalendarClient
    from adapters.idempotency import DailyLease
    from adapters.scheduler import SchedulerClient, make_prompt_schedule_name
    from adapters.user_state import UserStateRepository
except ImportError:
    from src.adapters.google_calendar import GoogleCalendarClient
    from src.adapters.idempotency import DailyLease
    from src.adapters.scheduler import SchedulerClient, make_prompt_schedule_name
    from src.adapters.user_state import UserStateRepository

logger = Logger()

# Configuration from environment
USER_STATE_TABLE = os.environ.get("USER_STATE_TABLE", "kairos-user-state")
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "kairos-idempotency")
PROMPT_SENDER_ARN = os.environ.get("PROMPT_SENDER_ARN", "")
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN", "")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

# MVP: Single user ID (later: query all active users)
MVP_USER_ID = os.environ.get("MVP_USER_ID", "user-001")

# Default settings
DEFAULT_TIMEZONE = "Europe/London"
DEFAULT_PROMPT_TIME = "17:30"
DEBRIEF_DURATION_MINUTES = 15


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for daily planning.

    Triggered by EventBridge Scheduler at 08:00 Europe/London.

    Args:
        event: EventBridge event (not used for MVP)
        context: Lambda context

    Returns:
        Response with status and details
    """
    logger.info("Daily planning started", extra={"event": event})

    # Get today's date in Europe/London timezone
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")

    # 1. Acquire daily lease to prevent duplicate runs
    lease = DailyLease(IDEMPOTENCY_TABLE, region=AWS_REGION)
    lease_key = DailyLease.make_key("daily-plan", MVP_USER_ID, today_str)
    request_id = getattr(context, "aws_request_id", "local-test")
    if not lease.try_acquire(lease_key, request_id):
        logger.info("Daily plan already executed for today")
        return {
            "statusCode": 200,
            "body": {"status": "already_planned", "date": today_str},
        }

    try:
        # 2. Get user state (or use defaults)
        user_repo = UserStateRepository(USER_STATE_TABLE, region=AWS_REGION)
        user_state = user_repo.get_user_state(MVP_USER_ID)

        preferred_time = DEFAULT_PROMPT_TIME

        if user_state:
            preferred_time = user_state.preferred_prompt_time or DEFAULT_PROMPT_TIME

            # Check if user has opted out
            if user_state.stopped:
                logger.info("User has stopped - skipping daily plan")
                return {
                    "statusCode": 200,
                    "body": {"status": "user_stopped", "user_id": MVP_USER_ID},
                }

        # 3. Compute today's debrief time
        hour, minute = map(int, preferred_time.split(":"))
        debrief_time_local = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If the time has already passed today, still create for today (user can move)
        # The prompt sender will handle the case where time is in the past

        debrief_start = debrief_time_local
        debrief_end = debrief_start + timedelta(minutes=DEBRIEF_DURATION_MINUTES)

        # Convert to UTC for scheduler
        debrief_time_utc = debrief_start.astimezone(ZoneInfo("UTC"))
        next_prompt_at_iso = debrief_time_utc.isoformat().replace("+00:00", "Z")

        # 4. Create/update Google Calendar debrief event
        calendar = GoogleCalendarClient.from_ssm()

        event_title = "📞 Kairos Debrief"
        event_description = (
            "Your daily debrief call.\n\n"
            "• Move this event to change the prompt time\n"
            "• Delete it to skip today's debrief\n"
            "• You'll receive an SMS prompt at this time"
        )

        extended_props = {
            "private": {
                "kairos_type": "debrief",
                "kairos_user_id": MVP_USER_ID,
                "kairos_date": today_str,
            }
        }

        # Check if we already have a debrief event for today
        debrief_event_id = None
        debrief_event_etag = None

        if user_state and user_state.debrief_event_id:
            # Try to update existing event
            try:
                existing = calendar.get_event(user_state.debrief_event_id)
                # Check if it's for today (via extended properties)
                ext_props = existing.get("extendedProperties", {}).get("private", {})
                if ext_props.get("kairos_date") == today_str:
                    # Update the existing event
                    updated = calendar.update_event(
                        event_id=user_state.debrief_event_id,
                        summary=event_title,
                        start_time=debrief_start,
                        end_time=debrief_end,
                    )
                    debrief_event_id = updated["id"]
                    debrief_event_etag = updated.get("etag")
                    logger.info(
                        "Updated existing debrief event", extra={"event_id": debrief_event_id}
                    )
            except Exception as e:
                logger.warning(
                    "Could not update existing event, will create new", extra={"error": str(e)}
                )

        if not debrief_event_id:
            # Create new event
            created = calendar.create_event(
                summary=event_title,
                start_time=debrief_start,
                end_time=debrief_end,
                description=event_description,
                extended_properties=extended_props,
            )
            debrief_event_id = created["id"]
            debrief_event_etag = created.get("etag")
            logger.info("Created new debrief event", extra={"event_id": debrief_event_id})

        # 4b. Set up/renew Google Calendar push notifications
        calendar_webhook_url = os.environ.get("CALENDAR_WEBHOOK_URL", "")
        channel_id = None
        channel_expiry = None

        if calendar_webhook_url:
            # Check if we need to (re)create the watch
            need_watch = True
            if user_state and user_state.google_channel_id and user_state.google_channel_expiry:
                # Parse expiry and check if still valid (with 1 day buffer)
                try:
                    expiry_dt = datetime.fromisoformat(
                        user_state.google_channel_expiry.replace("Z", "+00:00")
                    )
                    if expiry_dt > now + timedelta(days=1):
                        need_watch = False
                        logger.info(
                            "Calendar watch still valid",
                            extra={"expiry": user_state.google_channel_expiry},
                        )
                except (ValueError, AttributeError):
                    pass  # Invalid expiry, will recreate

            if need_watch:
                import uuid

                channel_id = str(uuid.uuid4())
                try:
                    watch_result = calendar.watch_calendar(
                        webhook_url=calendar_webhook_url,
                        channel_id=channel_id,
                    )
                    # Google returns expiration as milliseconds since epoch
                    expiration_ms = int(watch_result.get("expiration", 0))
                    if expiration_ms:
                        channel_expiry = datetime.fromtimestamp(
                            expiration_ms / 1000, tz=ZoneInfo("UTC")
                        ).isoformat()
                    logger.info(
                        "Created calendar watch",
                        extra={
                            "channel_id": channel_id,
                            "resource_id": watch_result.get("resourceId"),
                            "expiry": channel_expiry,
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to create calendar watch",
                        extra={"error": str(e)},
                    )
                    channel_id = None
                    channel_expiry = None

        # 5. Schedule one-time prompt sender trigger
        schedule_name = make_prompt_schedule_name(MVP_USER_ID, today_str)

        scheduler = SchedulerClient(region=AWS_REGION)
        scheduler.upsert_one_time_schedule(
            name=schedule_name,
            at_time_utc_iso=next_prompt_at_iso,
            target_arn=PROMPT_SENDER_ARN,
            payload={
                "user_id": MVP_USER_ID,
                "date": today_str,
                "scheduled_time": next_prompt_at_iso,
            },
            role_arn=SCHEDULER_ROLE_ARN,
            description=f"Kairos prompt for {MVP_USER_ID} on {today_str}",
        )
        logger.info(
            "Scheduled prompt sender",
            extra={"schedule_name": schedule_name, "time": next_prompt_at_iso},
        )

        # 6. Reset daily state in DynamoDB
        user_repo.reset_daily_state(
            user_id=MVP_USER_ID,
            next_prompt_at=next_prompt_at_iso,
            prompt_schedule_name=schedule_name,
            debrief_event_id=debrief_event_id,
            debrief_event_etag=debrief_event_etag,
            google_channel_id=channel_id,
            google_channel_expiry=channel_expiry,
        )
        logger.info("Reset daily state", extra={"user_id": MVP_USER_ID})

        # 7. Clean up stale schedules from prior days (best-effort)
        yesterday = now - timedelta(days=1)
        yesterday_str = yesterday.strftime("%Y-%m-%d")
        old_schedule_name = make_prompt_schedule_name(MVP_USER_ID, yesterday_str)
        scheduler.delete_schedule(old_schedule_name)

        return {
            "statusCode": 200,
            "body": {
                "status": "planned",
                "user_id": MVP_USER_ID,
                "date": today_str,
                "debrief_event_id": debrief_event_id,
                "next_prompt_at": next_prompt_at_iso,
                "schedule_name": schedule_name,
            },
        }

    except Exception:
        logger.exception("Daily planning failed")
        # Release lease so it can be retried
        lease.release(lease_key, request_id)
        raise
