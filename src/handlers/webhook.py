"""Lambda handler for the Bland AI webhook."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from aws_lambda_powertools import Logger
from pydantic import ValidationError

from adapters.anthropic_client import AnthropicSummarizer
from adapters.dynamodb import CallDeduplicator
from adapters.ses import SESPublisher
from adapters.ssm import get_parameter
from adapters.webhook_verify import verify_bland_signature
from core.models import BlandWebhookPayload, EventContext
from core.prompts import build_summarization_prompt

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="kairos-webhook")

# Lazy initialization for cold start optimization
_anthropic: AnthropicSummarizer | None = None
_ses: SESPublisher | None = None
_deduplicator: CallDeduplicator | None = None


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
            extra={"call_id": payload.call_id, "status": payload.status},
        )

    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("Invalid webhook payload", extra={"error": str(e)})
        return {"statusCode": 400, "body": json.dumps({"error": str(e)})}

    # Only process completed calls
    if payload.status != "completed":
        logger.info("Ignoring non-completed call", extra={"status": payload.status})
        return {"statusCode": 200, "body": json.dumps({"status": "ignored"})}

    # Check for duplicate processing
    deduplicator = get_deduplicator()
    if deduplicator and deduplicator.is_duplicate(payload.call_id):
        logger.warning("Duplicate call_id detected", extra={"call_id": payload.call_id})
        return {"statusCode": 200, "body": json.dumps({"status": "duplicate"})}

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
    message_id = ses.send_email(
        to_email=recipient_email,
        subject=f"Kairos Debrief: {event_context.subject}",
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
