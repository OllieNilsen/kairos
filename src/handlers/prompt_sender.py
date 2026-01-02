"""Prompt sender Lambda handler - sends SMS prompts for daily debriefs.

Triggered by EventBridge Scheduler one-time schedule at the user's preferred time.
Sends an SMS asking if user is ready for a debrief call.
The actual call is initiated by sms_webhook.py when user replies YES.

For retries (after unsuccessful calls), this handler directly initiates the call
since the user already consented.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger

# Support both Lambda and test import paths
try:
    from adapters.bland import BlandClient
    from adapters.idempotency import CallRetryDedup, SMSSendDedup
    from adapters.meetings_repo import MeetingsRepository
    from adapters.ssm import get_parameter
    from adapters.twilio_sms import TwilioClient
    from adapters.user_state import UserStateRepository
    from core.models import Meeting
except ImportError:
    from src.adapters.bland import BlandClient
    from src.adapters.idempotency import CallRetryDedup, SMSSendDedup
    from src.adapters.meetings_repo import MeetingsRepository
    from src.adapters.ssm import get_parameter
    from src.adapters.twilio_sms import TwilioClient
    from src.adapters.user_state import UserStateRepository
    from src.core.models import Meeting  # noqa: TC001 - used at runtime

logger = Logger()

# Configuration from environment
USER_STATE_TABLE = os.environ.get("USER_STATE_TABLE", "kairos-user-state")
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "kairos-idempotency")
MEETINGS_TABLE = os.environ.get("MEETINGS_TABLE", "kairos-meetings")
SSM_BLAND_API_KEY = os.environ.get("SSM_BLAND_API_KEY", "/kairos/bland-api-key")
SSM_TWILIO_ACCOUNT_SID = os.environ.get("SSM_TWILIO_ACCOUNT_SID", "/kairos/twilio-account-sid")
SSM_TWILIO_AUTH_TOKEN = os.environ.get("SSM_TWILIO_AUTH_TOKEN", "/kairos/twilio-auth-token")
SSM_TWILIO_FROM_NUMBER = os.environ.get("SSM_TWILIO_FROM_NUMBER", "/kairos/twilio-from-number")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

# SMS prompt message
SMS_PROMPT_TEMPLATE = """Hi! You have {count} meeting{s} to debrief today:
{meetings}

Ready for a quick call? Reply YES to start, or NO to skip today."""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for sending daily debrief prompts.

    For initial prompts: Sends SMS asking if user is ready for a debrief.
    For retries: Directly initiates a Bland call (user already consented).

    Args:
        event: Contains user_id, date, is_retry, retry_number
        context: Lambda context

    Returns:
        Response with status
    """
    logger.info("Prompt sender invoked", extra={"event": event})

    user_id = event.get("user_id", "user-001")
    date_str = event.get("date", datetime.now(UTC).strftime("%Y-%m-%d"))
    is_retry = event.get("is_retry", False)
    retry_number = event.get("retry_number", 0)

    # Route to appropriate handler
    if is_retry:
        return _handle_retry(user_id, date_str, retry_number)
    else:
        return _handle_initial_prompt(user_id, date_str)


def _handle_initial_prompt(user_id: str, date_str: str) -> dict[str, Any]:
    """Send initial SMS prompt to user.

    Args:
        user_id: User identifier
        date_str: Date string (YYYY-MM-DD)

    Returns:
        Response dict
    """
    # 1. Check SMS idempotency
    sms_dedup = SMSSendDedup(IDEMPOTENCY_TABLE, region=AWS_REGION)
    if not sms_dedup.try_send_daily_prompt(user_id, date_str):
        logger.info("SMS already sent today", extra={"user_id": user_id, "date": date_str})
        return {
            "statusCode": 200,
            "body": {"status": "already_sent", "user_id": user_id, "date": date_str},
        }

    try:
        # 2. Get user state
        user_repo = UserStateRepository(USER_STATE_TABLE, region=AWS_REGION)
        user_state = user_repo.get_user_state(user_id)

        if not user_state:
            logger.warning("User state not found", extra={"user_id": user_id})
            sms_dedup.release_daily_prompt(user_id, date_str)
            return {
                "statusCode": 404,
                "body": {"status": "error", "message": "User not found"},
            }

        # 3. Check if user can receive prompts
        can_prompt, reason = user_repo.can_prompt(user_state)
        if not can_prompt:
            logger.info("Cannot send prompt", extra={"reason": reason})
            return {
                "statusCode": 200,
                "body": {"status": reason, "user_id": user_id},
            }

        # 4. Load pending meetings
        meetings_repo = MeetingsRepository(MEETINGS_TABLE, region=AWS_REGION)
        pending_meetings = meetings_repo.get_pending_meetings(user_id)

        if not pending_meetings:
            logger.info("No pending meetings for today")
            sms_dedup.release_daily_prompt(user_id, date_str)
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
            try:
                phone_number = get_parameter("/kairos/user-phone-number", decrypt=False)
            except Exception:
                logger.error("No phone number configured for user")
                sms_dedup.release_daily_prompt(user_id, date_str)
                return {
                    "statusCode": 400,
                    "body": {"status": "error", "message": "No phone number configured"},
                }

        # 6. Build SMS prompt
        sms_body = _build_sms_prompt(pending_meetings)

        # 7. Send SMS via Twilio
        twilio = _get_twilio_client()
        message_sid = twilio.send_sms(phone_number, sms_body)

        logger.info("SMS sent", extra={"message_sid": message_sid, "phone": phone_number})

        # 8. Update user state - mark that we're awaiting a reply
        prompt_id = f"{user_id}#{date_str}"
        user_repo.record_prompt_sent(user_id, prompt_id)

        return {
            "statusCode": 202,
            "body": {
                "status": "sms_sent",
                "message_sid": message_sid,
                "user_id": user_id,
                "date": date_str,
                "meetings_count": len(pending_meetings),
            },
        }

    except Exception:
        logger.exception("Failed to send SMS prompt")
        sms_dedup.release_daily_prompt(user_id, date_str)
        raise


def _handle_retry(user_id: str, date_str: str, retry_number: int) -> dict[str, Any]:
    """Handle retry - directly initiate a call (user already consented).

    Args:
        user_id: User identifier
        date_str: Date string (YYYY-MM-DD)
        retry_number: Retry attempt number (1, 2, 3)

    Returns:
        Response dict
    """
    # 1. Check retry idempotency
    retry_dedup = CallRetryDedup(IDEMPOTENCY_TABLE, region=AWS_REGION)
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

    def release_func() -> None:
        retry_dedup.release(exec_key)

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

        # 3. Check if retry is still needed
        if user_state.stopped:
            logger.info("User has stopped - skipping retry")
            return {
                "statusCode": 200,
                "body": {"status": "user_stopped", "user_id": user_id},
            }

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

        # Check snooze
        if user_state.snooze_until:
            snooze_time = datetime.fromisoformat(user_state.snooze_until.replace("Z", "+00:00"))
            if datetime.now(UTC) < snooze_time:
                logger.info("User is snoozed", extra={"snooze_until": user_state.snooze_until})
                return {
                    "statusCode": 200,
                    "body": {"status": "snoozed", "user_id": user_id},
                }

        # 4. Load pending meetings
        meetings_repo = MeetingsRepository(MEETINGS_TABLE, region=AWS_REGION)
        pending_meetings = meetings_repo.get_pending_meetings(user_id)

        if not pending_meetings:
            logger.info("No pending meetings for retry")
            return {
                "statusCode": 200,
                "body": {"status": "no_meetings", "user_id": user_id, "date": date_str},
            }

        # 5. Get phone number
        phone_number = user_state.phone_number
        if not phone_number:
            try:
                phone_number = get_parameter("/kairos/user-phone-number", decrypt=False)
            except Exception:
                logger.error("No phone number configured for user")
                release_func()
                return {
                    "statusCode": 400,
                    "body": {"status": "error", "message": "No phone number configured"},
                }

        # 6. Build call context
        system_prompt = build_multi_meeting_prompt(pending_meetings)

        # 7. Initiate Bland call
        api_key = get_parameter(SSM_BLAND_API_KEY)
        bland = BlandClient(api_key)

        variables = {
            "user_id": user_id,
            "date": date_str,
            "meeting_ids": [m.meeting_id for m in pending_meetings],
            "meeting_titles": [m.title for m in pending_meetings],
        }

        call_id = asyncio.run(
            bland.initiate_call_raw(
                phone_number=phone_number,
                system_prompt=system_prompt,
                webhook_url=WEBHOOK_URL,
                variables=variables,
            )
        )

        logger.info(
            "Retry call initiated", extra={"call_id": call_id, "retry_number": retry_number}
        )

        # 8. Update user state
        user_repo.record_call_initiated(user_id, f"{user_id}#{date_str}")

        return {
            "statusCode": 202,
            "body": {
                "status": "call_initiated",
                "call_id": call_id,
                "user_id": user_id,
                "date": date_str,
                "retry_number": retry_number,
                "meetings_count": len(pending_meetings),
            },
        }

    except Exception:
        logger.exception("Failed to initiate retry call")
        release_func()
        raise


def _get_twilio_client() -> TwilioClient:
    """Get configured Twilio client."""
    account_sid = get_parameter(SSM_TWILIO_ACCOUNT_SID)
    auth_token = get_parameter(SSM_TWILIO_AUTH_TOKEN)
    from_number = get_parameter(SSM_TWILIO_FROM_NUMBER)
    return TwilioClient(account_sid, auth_token, from_number)


def _build_sms_prompt(meetings: list[Meeting]) -> str:
    """Build SMS prompt message listing meetings.

    Args:
        meetings: List of pending meetings

    Returns:
        SMS message body
    """
    # Build meeting list (max 3 to keep SMS short)
    meeting_lines = []
    for meeting in meetings[:3]:
        line = f"• {meeting.title}"
        if meeting.duration_minutes() > 0:
            line += f" ({meeting.duration_minutes()}min)"
        meeting_lines.append(line)

    if len(meetings) > 3:
        meeting_lines.append(f"• ...and {len(meetings) - 3} more")

    meetings_text = "\n".join(meeting_lines)

    return SMS_PROMPT_TEMPLATE.format(
        count=len(meetings),
        s="" if len(meetings) == 1 else "s",
        meetings=meetings_text,
    )


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
            ctx += f" (with {', '.join(meeting.attendee_names[:3])})"
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
        for name in meeting.attendee_names:
            if name not in seen:
                seen.add(name)
                result.append(name)
                if len(result) >= limit:
                    return result
    return result
