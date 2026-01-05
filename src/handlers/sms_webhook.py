"""Lambda handler for Twilio inbound SMS webhook.

Receives SMS replies from users and handles intents:
- YES/READY: Initiate a Bland AI debrief call
- NO: Snooze until tomorrow
- STOP: Opt out of all future messages
- UNKNOWN: Send clarification request
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from aws_lambda_powertools import Logger

# Support both Lambda and test import paths
try:
    from adapters.bland import BlandClient
    from adapters.idempotency import CallBatchDedup, InboundSMSDedup
    from adapters.llm import AnthropicAdapter
    from adapters.meetings_repo import MeetingsRepository
    from adapters.ssm import get_parameter
    from adapters.twilio_sms import (
        build_twiml_response,
        parse_twilio_webhook_body,
        verify_twilio_signature,
    )
    from adapters.user_state import UserStateRepository
    from adapters.users_repo import PhoneEnumerationRateLimitError, UsersRepository
    from core.models import SMSIntent, TwilioInboundSMS
    from core.sms_intent import parse_sms_intent
    from handlers.prompt_sender import build_multi_meeting_prompt
except ImportError:
    from src.adapters.bland import BlandClient
    from src.adapters.idempotency import CallBatchDedup, InboundSMSDedup
    from src.adapters.llm import AnthropicAdapter
    from src.adapters.meetings_repo import MeetingsRepository
    from src.adapters.ssm import get_parameter
    from src.adapters.twilio_sms import (
        build_twiml_response,
        parse_twilio_webhook_body,
        verify_twilio_signature,
    )
    from src.adapters.user_state import UserStateRepository
    from src.adapters.users_repo import PhoneEnumerationRateLimitError, UsersRepository
    from src.core.models import SMSIntent, TwilioInboundSMS
    from src.core.sms_intent import parse_sms_intent
    from src.handlers.prompt_sender import build_multi_meeting_prompt

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="kairos-sms-webhook")

# Configuration from environment
USERS_TABLE = os.environ.get("USERS_TABLE", "kairos-users")  # Slice 4B: Multi-user routing
USER_STATE_TABLE = os.environ.get("USER_STATE_TABLE", "kairos-user-state")
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "kairos-idempotency")
MEETINGS_TABLE = os.environ.get("MEETINGS_TABLE", "kairos-meetings")
SSM_TWILIO_AUTH_TOKEN = os.environ.get("SSM_TWILIO_AUTH_TOKEN", "/kairos/twilio-auth-token")
SSM_ANTHROPIC_API_KEY = os.environ.get("SSM_ANTHROPIC_API_KEY", "/kairos/anthropic-api-key")
SSM_BLAND_API_KEY = os.environ.get("SSM_BLAND_API_KEY", "/kairos/bland-api-key")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # Bland webhook URL
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

# Reply messages
REPLY_STARTING_CALL = "Great! Calling you now..."
REPLY_SNOOZED = "Got it. I'll check in again tomorrow."
REPLY_STOPPED = "You've been unsubscribed from Kairos. Reply START to re-enable."
REPLY_UNKNOWN = "I didn't understand that. Reply YES to start your debrief, or NO to skip today."
REPLY_NO_MEETINGS = "No meetings to debrief today. I'll check in after your next meeting day."
REPLY_ALREADY_CALLED = "Your debrief call is already in progress or completed for today."
REPLY_NOT_REGISTERED = "This phone number is not registered with Kairos. Please contact support."

# Lazy-initialized clients
_users_repo: UsersRepository | None = None  # Slice 4B: Multi-user routing
_user_repo: UserStateRepository | None = None
_inbound_dedup: InboundSMSDedup | None = None
_call_dedup: CallBatchDedup | None = None
_meetings_repo: MeetingsRepository | None = None
_llm_client: AnthropicAdapter | None = None


def get_users_repo() -> UsersRepository:
    """Get or create users repository (Slice 4B: Multi-user routing)."""
    global _users_repo
    if _users_repo is None:
        _users_repo = UsersRepository(USERS_TABLE)
    return _users_repo


def get_user_repo() -> UserStateRepository:
    """Get or create user state repository."""
    global _user_repo
    if _user_repo is None:
        _user_repo = UserStateRepository(USER_STATE_TABLE, region=AWS_REGION)
    return _user_repo


def get_inbound_dedup() -> InboundSMSDedup:
    """Get or create inbound SMS deduplicator."""
    global _inbound_dedup
    if _inbound_dedup is None:
        _inbound_dedup = InboundSMSDedup(IDEMPOTENCY_TABLE, region=AWS_REGION)
    return _inbound_dedup


def get_call_dedup() -> CallBatchDedup:
    """Get or create call batch deduplicator."""
    global _call_dedup
    if _call_dedup is None:
        _call_dedup = CallBatchDedup(IDEMPOTENCY_TABLE, region=AWS_REGION)
    return _call_dedup


def get_meetings_repo() -> MeetingsRepository:
    """Get or create meetings repository."""
    global _meetings_repo
    if _meetings_repo is None:
        _meetings_repo = MeetingsRepository(MEETINGS_TABLE, region=AWS_REGION)
    return _meetings_repo


def get_llm_client() -> AnthropicAdapter:
    """Get or create LLM client."""
    global _llm_client
    if _llm_client is None:
        api_key = get_parameter(SSM_ANTHROPIC_API_KEY)
        _llm_client = AnthropicAdapter(api_key)
    return _llm_client


def _twiml_response(message: str | None = None, status: int = 200) -> dict[str, Any]:
    """Build Lambda response with TwiML body."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/xml"},
        "body": build_twiml_response(message),
    }


@logger.inject_lambda_context
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Handle inbound SMS from Twilio.

    Expected event format (Lambda Function URL):
    {
        "body": "Body=Yes&From=...",
        "headers": {"x-twilio-signature": "..."},
        "requestContext": {"http": {"path": "/sms-webhook"}}
    }
    """
    # 1. Extract raw body and headers
    raw_body = event.get("body", "")
    headers = event.get("headers", {})

    # Handle base64-encoded body (API Gateway/Function URL)
    if event.get("isBase64Encoded"):
        import base64

        raw_body = base64.b64decode(raw_body).decode("utf-8")

    # 2. Parse webhook body
    params = parse_twilio_webhook_body(raw_body)
    if not params.get("MessageSid"):
        logger.warning("Missing MessageSid in webhook body")
        return _twiml_response(status=400)

    # 3. Verify Twilio signature
    signature = headers.get("x-twilio-signature", headers.get("X-Twilio-Signature", ""))
    ssm_auth_token = os.environ.get("SSM_TWILIO_AUTH_TOKEN")

    if ssm_auth_token:
        auth_token = get_parameter(ssm_auth_token)
        # Build the webhook URL from the request
        # For Function URLs, we need to reconstruct the full URL
        webhook_url = _build_webhook_url(event)

        if not verify_twilio_signature(auth_token, signature, webhook_url, params):
            logger.warning("Invalid Twilio signature")
            return _twiml_response(status=401)

        logger.info("Twilio signature verified")

    # 4. Parse into model
    try:
        sms = TwilioInboundSMS.model_validate(params)
    except Exception as e:
        logger.warning("Failed to parse SMS", extra={"error": str(e)})
        return _twiml_response(status=400)

    logger.info(
        "Received SMS",
        extra={
            "message_sid": sms.MessageSid,
            "from": sms.From,
            "body_preview": sms.Body[:50] if sms.Body else "",
        },
    )

    # 5. Check idempotency
    dedup = get_inbound_dedup()
    if not dedup.try_process_message(sms.MessageSid):
        logger.info("Duplicate message - already processed", extra={"message_sid": sms.MessageSid})
        return _twiml_response()

    # 6. Look up user by phone number (Slice 4B: Multi-user routing)
    users_repo = get_users_repo()
    try:
        user_id = users_repo.get_user_by_phone(sms.From, enforce_rate_limit=True)
    except PhoneEnumerationRateLimitError:
        logger.warning(
            "Phone enumeration rate limit exceeded",
            extra={"phone_hint": sms.From[-4:]},  # Log only last 4 digits
        )
        return _twiml_response(status=429)

    if not user_id:
        logger.warning(
            "Phone number not registered",
            extra={"phone_hint": sms.From[-4:]},  # Log only last 4 digits
        )
        return _twiml_response(REPLY_NOT_REGISTERED)

    logger.info("Routed SMS to user", extra={"user_id": user_id})

    user_repo = get_user_repo()
    user_state = user_repo.get_user_state(user_id)

    if not user_state:
        logger.warning("User not found", extra={"user_id": user_id})
        return _twiml_response(REPLY_UNKNOWN)

    # 7. Parse intent using LLM
    llm_client = get_llm_client()
    intent = parse_sms_intent(sms.Body, llm_client)
    logger.info("Parsed intent", extra={"intent": intent.value, "body": sms.Body})

    # 8. Handle intent
    if intent == SMSIntent.YES or intent == SMSIntent.READY:
        return _handle_ready(user_id, user_state.phone_number or sms.From)

    if intent == SMSIntent.NO:
        return _handle_no(user_id)

    if intent == SMSIntent.STOP:
        return _handle_stop(user_id)

    # UNKNOWN
    return _twiml_response(REPLY_UNKNOWN)


def _handle_ready(user_id: str, phone_number: str) -> dict[str, Any]:
    """Handle YES/READY intent - initiate a debrief call.

    Args:
        user_id: The user identifier
        phone_number: User's phone number (E.164)

    Returns:
        TwiML response
    """
    import asyncio

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    # Check call idempotency
    call_dedup = get_call_dedup()
    if not call_dedup.try_initiate_call(user_id, date_str):
        logger.info("Call already initiated today")
        return _twiml_response(REPLY_ALREADY_CALLED)

    try:
        # Get pending meetings
        meetings_repo = get_meetings_repo()
        pending_meetings = meetings_repo.get_pending_meetings(user_id)

        if not pending_meetings:
            logger.info("No pending meetings")
            call_dedup.release_call(user_id, date_str)
            return _twiml_response(REPLY_NO_MEETINGS)

        logger.info(
            "Found pending meetings",
            extra={"count": len(pending_meetings)},
        )

        # Build system prompt
        system_prompt = build_multi_meeting_prompt(pending_meetings)

        # Initiate Bland call
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

        logger.info("Call initiated", extra={"call_id": call_id})

        # Update user state
        user_repo = get_user_repo()
        user_repo.record_call_initiated(user_id, f"{user_id}#{date_str}")

        return _twiml_response(REPLY_STARTING_CALL)

    except Exception:
        logger.exception("Failed to initiate call")
        call_dedup.release_call(user_id, date_str)
        return _twiml_response("Sorry, there was an error. Please try again.")


def _handle_no(user_id: str) -> dict[str, Any]:
    """Handle NO intent - snooze until tomorrow.

    Args:
        user_id: The user identifier

    Returns:
        TwiML response
    """
    # Snooze until 6am tomorrow
    tomorrow_6am = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )

    user_repo = get_user_repo()
    user_repo.set_snooze(user_id, tomorrow_6am.isoformat())

    logger.info("User snoozed", extra={"until": tomorrow_6am.isoformat()})

    return _twiml_response(REPLY_SNOOZED)


def _handle_stop(user_id: str) -> dict[str, Any]:
    """Handle STOP intent - opt out of all messages.

    Args:
        user_id: The user identifier

    Returns:
        TwiML response
    """
    user_repo = get_user_repo()
    user_repo.set_stop(user_id, stop=True)

    logger.info("User stopped", extra={"user_id": user_id})

    return _twiml_response(REPLY_STOPPED)


def _build_webhook_url(event: dict[str, Any]) -> str:
    """Build the full webhook URL from Lambda Function URL event.

    Args:
        event: Lambda event

    Returns:
        Full URL string for signature verification
    """
    request_context = event.get("requestContext", {})
    http_context = request_context.get("http", {})

    # Get domain from requestContext
    domain_name = request_context.get("domainName", "")
    path = http_context.get("path", "/")

    if domain_name:
        return f"https://{domain_name}{path}"

    # Fallback to headers
    host = event.get("headers", {}).get("host", "")
    if host:
        return f"https://{host}{path}"

    # Last resort - use configured URL
    return os.environ.get("SMS_WEBHOOK_URL", "")
