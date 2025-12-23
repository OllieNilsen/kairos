"""Lambda handler for the Bland AI webhook."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from aws_lambda_powertools import Logger
from pydantic import ValidationError

from adapters.anthropic_client import AnthropicSummarizer
from adapters.dynamodb import CallDeduplicator
from adapters.idempotency import CallRetryDedup
from adapters.scheduler import SchedulerClient, make_retry_schedule_name
from adapters.ses import SESPublisher
from adapters.ssm import get_parameter
from adapters.user_state import UserStateRepository
from adapters.webhook_verify import verify_bland_signature
from core.models import BlandWebhookPayload, EventContext
from core.prompts import build_summarization_prompt

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="kairos-webhook")

# Retry configuration
RETRY_DELAY_MINUTES = 15
MAX_RETRIES = 3

# Voicemail detection keywords
VOICEMAIL_KEYWORDS = [
    "voicemail",
    "leave a message",
    "leave your message",
    "not available",
    "please leave",
    "after the beep",
    "after the tone",
    "mailbox",
]

# Lazy initialization for cold start optimization
_anthropic: AnthropicSummarizer | None = None
_ses: SESPublisher | None = None
_deduplicator: CallDeduplicator | None = None
_user_repo: UserStateRepository | None = None
_retry_dedup: CallRetryDedup | None = None
_scheduler: SchedulerClient | None = None


def get_anthropic() -> AnthropicSummarizer:
    """Get or create the Anthropic client."""
    global _anthropic
    if _anthropic is None:
        ssm_param_name = os.environ["SSM_ANTHROPIC_API_KEY"]
        api_key = get_parameter(ssm_param_name)
        _anthropic = AnthropicSummarizer(api_key)
    return _anthropic


def get_ses() -> SESPublisher:
    """Get or create the SES publisher."""
    global _ses
    if _ses is None:
        sender_email = os.environ["SENDER_EMAIL"]
        _ses = SESPublisher(sender_email)
    return _ses


def get_deduplicator() -> CallDeduplicator | None:
    """Get or create the deduplicator (if table is configured)."""
    global _deduplicator
    table_name = os.environ.get("DEDUP_TABLE_NAME")
    if not table_name:
        return None
    if _deduplicator is None:
        _deduplicator = CallDeduplicator(table_name)
    return _deduplicator


def get_user_repo() -> UserStateRepository | None:
    """Get or create the user state repository."""
    global _user_repo
    table_name = os.environ.get("USER_STATE_TABLE")
    if not table_name:
        return None
    if _user_repo is None:
        _user_repo = UserStateRepository(table_name)
    return _user_repo


def get_retry_dedup() -> CallRetryDedup | None:
    """Get or create the retry deduplicator."""
    global _retry_dedup
    table_name = os.environ.get("IDEMPOTENCY_TABLE")
    if not table_name:
        return None
    if _retry_dedup is None:
        _retry_dedup = CallRetryDedup(table_name)
    return _retry_dedup


def get_scheduler() -> SchedulerClient:
    """Get or create the scheduler client."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerClient()
    return _scheduler


@logger.inject_lambda_context
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Handle Bland AI webhook callbacks.

    Expected event format (Lambda Function URL):
    {
        "body": "{...bland webhook payload...}",
        "headers": {"x-webhook-signature": "..."},
        "requestContext": {...}
    }
    """
    # Get raw body for signature verification (before JSON parsing)
    raw_body = event.get("body", "{}")

    # Verify webhook signature (if secret is configured)
    ssm_webhook_secret = os.environ.get("SSM_BLAND_WEBHOOK_SECRET")
    if ssm_webhook_secret:
        webhook_secret = get_parameter(ssm_webhook_secret)
        headers = event.get("headers", {})
        signature = headers.get("x-webhook-signature", "")

        if not verify_bland_signature(webhook_secret, raw_body, signature):
            logger.warning("Invalid webhook signature")
            return {"statusCode": 401, "body": json.dumps({"error": "Invalid signature"})}

        logger.info("Webhook signature verified")

    try:
        # Parse and validate webhook payload
        body = raw_body
        if isinstance(body, str):
            body = json.loads(body)

        payload = BlandWebhookPayload.model_validate(body)
        logger.info(
            "Received webhook",
            extra={
                "call_id": payload.call_id,
                "status": payload.status,
                "call_length": payload.call_length,
            },
        )

    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("Invalid webhook payload", extra={"error": str(e)})
        return {"statusCode": 400, "body": json.dumps({"error": str(e)})}

    # Check for duplicate processing
    deduplicator = get_deduplicator()
    if deduplicator and deduplicator.is_duplicate(payload.call_id):
        logger.warning("Duplicate call_id detected", extra={"call_id": payload.call_id})
        return {"statusCode": 200, "body": json.dumps({"status": "duplicate"})}

    # Extract user context from variables (passed through from prompt_sender)
    metadata = payload.variables.get("metadata", payload.variables)
    user_id = metadata.get("user_id", "user-001")
    date_str = metadata.get("date", datetime.now(UTC).strftime("%Y-%m-%d"))

    # Check if call was successful
    call_successful = _is_call_successful(payload)

    if not call_successful:
        # Handle unsuccessful call - schedule retry if possible
        return _handle_unsuccessful_call(payload, user_id, date_str)

    # Call was successful - proceed with summary
    return _handle_successful_call(payload, user_id)


def _is_call_successful(payload: BlandWebhookPayload) -> bool:
    """Determine if a call was successful based on various indicators.

    Args:
        payload: The Bland webhook payload

    Returns:
        True if call was successful, False if it should be retried
    """
    # Check status
    if payload.status != "completed":
        logger.info("Call not completed", extra={"status": payload.status})
        return False

    # Check duration - calls under 30 seconds are likely unsuccessful
    # call_length is in minutes from Bland
    duration_seconds = (payload.call_length or 0) * 60
    if duration_seconds < 30:
        logger.info(
            "Call too short - likely unsuccessful",
            extra={"duration_seconds": duration_seconds},
        )
        return False

    # Check transcript for voicemail indicators
    transcript_lower = payload.concatenated_transcript.lower()
    for keyword in VOICEMAIL_KEYWORDS:
        if keyword in transcript_lower:
            logger.info(
                "Voicemail detected in transcript",
                extra={"keyword": keyword},
            )
            return False

    return True


def _handle_unsuccessful_call(
    payload: BlandWebhookPayload,
    user_id: str,
    date_str: str,
) -> dict[str, Any]:
    """Handle an unsuccessful call by scheduling a retry if possible.

    Args:
        payload: The Bland webhook payload
        user_id: The user identifier
        date_str: The date string (YYYY-MM-DD)

    Returns:
        HTTP response dict
    """
    user_repo = get_user_repo()
    if not user_repo:
        logger.warning("User state table not configured - cannot schedule retry")
        return {"statusCode": 200, "body": json.dumps({"status": "no_retry_config"})}

    # Get current user state
    user_state = user_repo.get_user_state(user_id)
    if not user_state:
        logger.warning("User not found", extra={"user_id": user_id})
        return {"statusCode": 200, "body": json.dumps({"status": "user_not_found"})}

    # Check if we can retry
    can_retry, reason = user_repo.can_retry(user_state, MAX_RETRIES)
    if not can_retry:
        logger.info(
            "Cannot retry call",
            extra={"reason": reason, "retries_today": user_state.retries_today},
        )
        # Still generate summary for the unsuccessful call
        return _handle_successful_call(payload, user_id, prefix="Call unsuccessful - ")

    # Schedule retry
    retry_number = user_state.retries_today + 1
    retry_time = datetime.now(UTC) + timedelta(minutes=RETRY_DELAY_MINUTES)
    retry_schedule_name = make_retry_schedule_name(user_id, date_str, retry_number)

    # Check idempotency - don't schedule same retry twice
    retry_dedup = get_retry_dedup()
    if retry_dedup and not retry_dedup.try_schedule_retry(user_id, date_str, retry_number):
        logger.info(
            "Retry already scheduled",
            extra={"retry_number": retry_number},
        )
        return {"statusCode": 200, "body": json.dumps({"status": "retry_already_scheduled"})}

    try:
        # Get scheduler config
        prompt_sender_arn = os.environ.get("PROMPT_SENDER_LAMBDA_ARN", "")
        scheduler_role_arn = os.environ.get("SCHEDULER_ROLE_ARN", "")

        if not prompt_sender_arn or not scheduler_role_arn:
            logger.warning("Scheduler not configured - cannot schedule retry")
            return {"statusCode": 200, "body": json.dumps({"status": "scheduler_not_configured"})}

        # Create retry schedule
        scheduler = get_scheduler()
        scheduler.upsert_one_time_schedule(
            name=retry_schedule_name,
            at_time_utc_iso=retry_time.isoformat().replace("+00:00", "Z"),
            target_arn=prompt_sender_arn,
            payload={
                "user_id": user_id,
                "date": date_str,
                "is_retry": True,
                "retry_number": retry_number,
            },
            role_arn=scheduler_role_arn,
            description=f"Kairos retry {retry_number} for {user_id}",
        )

        # Update user state
        user_repo.record_retry_scheduled(
            user_id=user_id,
            next_retry_at=retry_time.isoformat(),
            retry_schedule_name=retry_schedule_name,
        )

        logger.info(
            "Retry scheduled",
            extra={
                "retry_number": retry_number,
                "retry_time": retry_time.isoformat(),
                "schedule_name": retry_schedule_name,
            },
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "status": "retry_scheduled",
                    "retry_number": retry_number,
                    "retry_time": retry_time.isoformat(),
                }
            ),
        }

    except Exception as e:
        logger.exception("Failed to schedule retry")
        # Release idempotency key so it can be retried
        if retry_dedup:
            retry_dedup.release_retry(user_id, date_str, retry_number)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def _handle_successful_call(
    payload: BlandWebhookPayload,
    user_id: str,
    prefix: str = "",
) -> dict[str, Any]:
    """Handle a successful call by generating and sending summary.

    Args:
        payload: The Bland webhook payload
        user_id: The user identifier
        prefix: Optional prefix for the email subject

    Returns:
        HTTP response dict
    """
    # Mark call as successful in user state
    user_repo = get_user_repo()
    if user_repo:
        user_repo.record_call_success(user_id)
        user_repo.clear_retry_schedule(user_id)

    # Extract event context from variables (passed through from trigger)
    event_context = _extract_event_context(payload)

    # Summarize the transcript
    anthropic = get_anthropic()
    summarization_prompt = build_summarization_prompt(
        transcript=payload.concatenated_transcript,
        context=event_context,
    )

    summary = anthropic.summarize(
        transcript=payload.concatenated_transcript,
        system_prompt="You are a concise summarizer. Output only the summary, no preamble.",
        user_prompt=summarization_prompt,
    )
    logger.info("Generated summary", extra={"length": len(summary), "summary": summary})

    # Send email notification
    ses = get_ses()
    recipient_email = os.environ["RECIPIENT_EMAIL"]
    subject = f"{prefix}Kairos Debrief: {event_context.subject}"
    message_id = ses.send_email(
        to_email=recipient_email,
        subject=subject,
        body=summary,
    )
    logger.info("Sent email", extra={"message_id": message_id})

    return {
        "statusCode": 200,
        "body": json.dumps({"status": "processed", "message_id": message_id}),
    }


def _extract_event_context(payload: BlandWebhookPayload) -> EventContext:
    """Extract EventContext from webhook variables.

    Bland sends variables in nested structure:
    - variables.metadata.event_context (from our metadata field)
    - or variables.event_context (flat)

    Falls back to defaults if not present.
    """
    try:
        # Try nested path first (Bland wraps our metadata)
        metadata = payload.variables.get("metadata", {})
        if isinstance(metadata, dict):
            context_json = metadata.get("event_context", "")
            if context_json:
                return EventContext.model_validate_json(context_json)

        # Try flat path
        context_json = payload.variables.get("event_context", "")
        if context_json:
            if isinstance(context_json, str):
                return EventContext.model_validate_json(context_json)
            elif isinstance(context_json, dict):
                return EventContext.model_validate(context_json)

    except Exception as e:
        logger.warning("Could not extract event context", extra={"error": str(e)})

    # Fallback to defaults
    return EventContext(
        event_type="general",
        subject="Debrief Call",
        participants=[],
    )
