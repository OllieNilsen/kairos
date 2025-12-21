"""Lambda handler for the Bland AI webhook."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from aws_lambda_powertools import Logger
from pydantic import ValidationError

from src.adapters.anthropic_client import AnthropicSummarizer
from src.adapters.sns import SNSPublisher
from src.core.models import BlandWebhookPayload, EventContext
from src.core.prompts import build_summarization_prompt

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="kairos-webhook")

# Lazy initialization for cold start optimization
_anthropic: AnthropicSummarizer | None = None
_sns: SNSPublisher | None = None


def get_anthropic() -> AnthropicSummarizer:
    """Get or create the Anthropic client."""
    global _anthropic
    if _anthropic is None:
        api_key = os.environ["ANTHROPIC_API_KEY"]
        _anthropic = AnthropicSummarizer(api_key)
    return _anthropic


def get_sns() -> SNSPublisher:
    """Get or create the SNS publisher."""
    global _sns
    if _sns is None:
        topic_arn = os.environ["SNS_TOPIC_ARN"]
        _sns = SNSPublisher(topic_arn)
    return _sns


@logger.inject_lambda_context
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Handle Bland AI webhook callbacks.

    Expected event format (Lambda Function URL):
    {
        "body": "{...bland webhook payload...}",
        "requestContext": {...}
    }
    """
    try:
        # Parse and validate webhook payload
        body = event.get("body", "{}")
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
    logger.info("Generated summary", extra={"length": len(summary)})

    # Send SMS notification
    sns = get_sns()
    message_id = sns.send_sms(
        message=summary[:300],  # SMS length limit
        phone_number=payload.to,
    )
    logger.info("Sent SMS", extra={"message_id": message_id})

    return {
        "statusCode": 200,
        "body": json.dumps({"status": "processed", "message_id": message_id}),
    }


def _extract_event_context(payload: BlandWebhookPayload) -> EventContext:
    """Extract EventContext from webhook variables.

    Falls back to defaults if not present.
    """
    try:
        context_json = payload.variables.get("event_context", "{}")
        return EventContext.model_validate_json(context_json)
    except Exception:
        logger.warning("Could not extract event context, using defaults")
        return EventContext(
            event_type="general",
            subject="Debrief Call",
            participants=[],
        )
