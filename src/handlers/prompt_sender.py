"""Prompt sender Lambda handler - initiates daily debrief calls.

Triggered by EventBridge Scheduler one-time schedule at the user's preferred time.
Bypasses SMS prompting and directly initiates the Bland call.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aws_lambda_powertools import Logger

if TYPE_CHECKING:
    from collections.abc import Callable

# Support both Lambda and test import paths
try:
    from adapters.bland import BlandClient
    from adapters.idempotency import CallBatchDedup, CallRetryDedup
    from adapters.meetings_repo import MeetingsRepository
    from adapters.ssm import get_parameter
    from adapters.user_state import UserStateRepository
    from core.models import Meeting
except ImportError:
    from src.adapters.bland import BlandClient
    from src.adapters.idempotency import CallBatchDedup, CallRetryDedup
    from src.adapters.meetings_repo import MeetingsRepository
    from src.adapters.ssm import get_parameter
    from src.adapters.user_state import UserStateRepository
    from src.core.models import Meeting  # noqa: TC001 - used at runtime

logger = Logger()

# Configuration from environment
USER_STATE_TABLE = os.environ.get("USER_STATE_TABLE", "kairos-user-state")
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "kairos-idempotency")
MEETINGS_TABLE = os.environ.get("MEETINGS_TABLE", "kairos-meetings")
SSM_BLAND_API_KEY = os.environ.get("SSM_BLAND_API_KEY", "/kairos/bland-api-key")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

# Default interview questions for multi-meeting debriefs
DEFAULT_INTERVIEW_PROMPTS = [
    "What were the key outcomes from your meetings today?",
    "Were there any important decisions made?",
    "What action items came out of these meetings?",
    "Anything else worth noting?",
]


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for initiating daily debrief calls.

    Triggered by one-time EventBridge Scheduler at the user's prompt time,
    or by a retry schedule after an unsuccessful call.

    Args:
        event: Contains user_id, date, is_retry, retry_number
        context: Lambda context

    Returns:
        Response with status
    """
    import asyncio

    logger.info("Prompt sender invoked", extra={"event": event})

    user_id = event.get("user_id", "user-001")
    date_str = event.get("date", datetime.now(UTC).strftime("%Y-%m-%d"))
    is_retry = event.get("is_retry", False)
    retry_number = event.get("retry_number", 0)

    # 1. Check idempotency
    release_func: Callable[[], None]

    if is_retry:
        # For retries, use retry-specific idempotency
        retry_dedup = CallRetryDedup(IDEMPOTENCY_TABLE, region=AWS_REGION)
        # The retry was already marked when scheduling, but check if already executed
        # We use a separate key pattern for execution vs scheduling
        exec_key = f"call-retry-exec:{user_id}#{date_str}#{retry_number}"
        if not retry_dedup.try_acquire(exec_key):
            logger.info(
                "Retry already executed",
                extra={"user_id": user_id, "date": date_str, "retry_number": retry_number},
            )
            return {
                "statusCode": 200,
                "body": {"status": "retry_already_executed", "retry_number": retry_number},
            }

        def _release_retry() -> None:
            retry_dedup.release(exec_key)

        release_func = _release_retry
    else:
        # For initial call, use daily batch idempotency
        call_dedup = CallBatchDedup(IDEMPOTENCY_TABLE, region=AWS_REGION)
        if not call_dedup.try_initiate_call(user_id, date_str):
            logger.info(
                "Call already initiated for today", extra={"user_id": user_id, "date": date_str}
            )
            return {
                "statusCode": 200,
                "body": {"status": "already_called", "user_id": user_id, "date": date_str},
            }

        def _release_call() -> None:
            call_dedup.release_call_batch(user_id, date_str)

        release_func = _release_call

    try:
        # 2. Get user state
        user_repo = UserStateRepository(USER_STATE_TABLE, region=AWS_REGION)
        user_state = user_repo.get_user_state(user_id)

        if not user_state:
            logger.warning("User state not found", extra={"user_id": user_id})
            release_func()
            return {
                "statusCode": 404,
                "body": {"status": "error", "message": "User not found"},
            }

        # 3. Check if user can receive calls
        if user_state.stopped:
            logger.info("User has stopped - skipping call")
            return {
                "statusCode": 200,
                "body": {"status": "user_stopped", "user_id": user_id},
            }

        # For retries, check call_successful instead of daily_call_made
        if is_retry:
            if user_state.call_successful:
                logger.info("Call already successful - skipping retry")
                return {
                    "statusCode": 200,
                    "body": {"status": "call_already_successful", "user_id": user_id},
                }
            if user_state.retries_today >= 3:
                logger.info("Max retries reached")
                return {
                    "statusCode": 200,
                    "body": {"status": "max_retries_reached", "user_id": user_id},
                }
        else:
            # Initial call - check if successful call already made
            if user_state.daily_call_made and user_state.call_successful:
                logger.info("Daily call already successful via user state")
                return {
                    "statusCode": 200,
                    "body": {"status": "already_called", "user_id": user_id},
                }

        # Check snooze
        if user_state.snooze_until:
            snooze_time = datetime.fromisoformat(user_state.snooze_until.replace("Z", "+00:00"))
            if datetime.now(UTC) < snooze_time:
                logger.info("User is snoozed", extra={"snooze_until": user_state.snooze_until})
                return {
                    "statusCode": 200,
                    "body": {"status": "snoozed", "user_id": user_id},
                }

        # 4. Load pending meetings for today
        meetings_repo = MeetingsRepository(MEETINGS_TABLE, region=AWS_REGION)
        pending_meetings = meetings_repo.get_pending_meetings(user_id)

        if not pending_meetings:
            logger.info("No pending meetings for today")
            return {
                "statusCode": 200,
                "body": {"status": "no_meetings", "user_id": user_id, "date": date_str},
            }

        logger.info(
            "Found pending meetings",
            extra={"count": len(pending_meetings), "meetings": [m.title for m in pending_meetings]},
        )

        # 5. Get user phone number
        phone_number = user_state.phone_number
        if not phone_number:
            # Fall back to SSM parameter for MVP
            try:
                phone_number = get_parameter("/kairos/user-phone-number", decrypt=False)
            except Exception:
                logger.error("No phone number configured for user")
                release_func()
                return {
                    "statusCode": 400,
                    "body": {"status": "error", "message": "No phone number configured"},
                }

        # 6. Build multi-meeting call context
        system_prompt = build_multi_meeting_prompt(pending_meetings)

        # 7. Initiate Bland call
        api_key = get_parameter(SSM_BLAND_API_KEY)
        bland = BlandClient(api_key)

        # Store meeting IDs in variables so webhook can mark them debriefed
        variables = {
            "user_id": user_id,
            "date": date_str,
            "meeting_ids": [m.meeting_id for m in pending_meetings],
            "meeting_titles": [m.title for m in pending_meetings],
        }

        call_id = asyncio.get_event_loop().run_until_complete(
            bland.initiate_call_raw(
                phone_number=phone_number,
                system_prompt=system_prompt,
                webhook_url=WEBHOOK_URL,
                variables=variables,
            )
        )

        logger.info("Call initiated", extra={"call_id": call_id, "phone": phone_number})

        # 8. Update user state
        user_repo.record_call_initiated(
            user_id=user_id,
            batch_id=f"{user_id}#{date_str}",
        )

        return {
            "statusCode": 202,
            "body": {
                "status": "call_initiated",
                "call_id": call_id,
                "user_id": user_id,
                "date": date_str,
                "meetings_count": len(pending_meetings),
            },
        }

    except Exception:
        logger.exception("Failed to initiate call")
        # Release idempotency key so it can be retried
        release_func()
        raise


def build_multi_meeting_prompt(meetings: list[Meeting]) -> str:
    """Build system prompt for a multi-meeting debrief call.

    Args:
        meetings: List of meetings to debrief

    Returns:
        System prompt for the Bland AI voice agent
    """
    meeting_contexts = []
    for i, meeting in enumerate(meetings, 1):
        ctx = f"{i}. {meeting.title}"
        if meeting.attendees:
            ctx += f" (with {', '.join(meeting.attendees[:3])})"
        ctx += f" - {meeting.duration_minutes()} min"
        meeting_contexts.append(ctx)

    meetings_list = "\n".join(meeting_contexts)

    return f"""You are Kairos, a professional AI assistant helping with end-of-day meeting debriefs.

TODAY'S MEETINGS TO DEBRIEF:
{meetings_list}

YOUR TASK:
Conduct a brief, focused debrief covering all {len(meetings)} meeting(s). Ask about:
1. Key outcomes and decisions from today's meetings
2. Important action items and who's responsible
3. Any blockers or concerns raised
4. Anything else noteworthy

STYLE:
- Be conversational but efficient
- You can discuss multiple meetings together or ask about specific ones
- Acknowledge responses and probe for details when useful
- Keep the call under 5 minutes total
- End with "Thanks, I'll send you a summary shortly."

IMPORTANT:
- If user mentions a specific meeting, note which one for the summary
- Focus on actionable takeaways, not just recaps
- It's OK if some meetings had no notable outcomes
"""


def _collect_unique_attendees(meetings: list[Meeting], limit: int = 10) -> list[str]:
    """Collect unique attendee names from all meetings."""
    seen: set[str] = set()
    result: list[str] = []
    for meeting in meetings:
        for attendee in meeting.attendees:
            if attendee not in seen:
                seen.add(attendee)
                result.append(attendee)
                if len(result) >= limit:
                    return result
    return result
