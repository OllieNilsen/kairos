"""Lambda handler for the trigger endpoint."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from aws_lambda_powertools import Logger
from pydantic import ValidationError

# Support both Lambda (adapters...) and test (src.adapters...) import paths
try:
    from adapters.bland import BlandClient
    from adapters.ssm import get_parameter
    from core.models import TriggerPayload, TriggerResponse
    from core.prompts import build_debrief_system_prompt
except ImportError:
    from src.adapters.bland import BlandClient
    from src.adapters.ssm import get_parameter
    from src.core.models import TriggerPayload, TriggerResponse
    from src.core.prompts import build_debrief_system_prompt

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="kairos-trigger")

# Initialize client lazily for cold start optimization
_bland_client: BlandClient | None = None


def get_bland_client() -> BlandClient:
    """Get or create the Bland AI client."""
    global _bland_client
    if _bland_client is None:
        ssm_param_name = os.environ["SSM_BLAND_API_KEY"]
        api_key = get_parameter(ssm_param_name)
        _bland_client = BlandClient(api_key)
    return _bland_client


@logger.inject_lambda_context
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Handle incoming trigger requests.

    Expected event format (Lambda Function URL):
    {
        "body": "{...json payload...}",
        "requestContext": {...}
    }
    """
    import asyncio

    try:
        # Parse and validate request body
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        payload = TriggerPayload.model_validate(body)
        logger.info("Validated trigger payload", extra={"phone": payload.phone_number})

    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("Invalid request payload", extra={"error": str(e)})
        return _response(
            400,
            TriggerResponse(status="error", message=f"Invalid payload: {e}"),
        )

    # Build system prompt for voice agent
    system_prompt = build_debrief_system_prompt(
        context=payload.event_context,
        prompts=payload.interview_prompts,
    )

    # Initiate the call
    webhook_url = os.environ["WEBHOOK_URL"]
    bland = get_bland_client()

    try:
        call_id = asyncio.run(bland.initiate_call(payload, system_prompt, webhook_url))
        logger.info("Call initiated", extra={"call_id": call_id})

        return _response(
            202,
            TriggerResponse(
                status="initiated",
                call_id=call_id,
                message="Call initiated. You will receive an SMS summary when complete.",
            ),
        )

    except Exception as e:
        logger.exception("Failed to initiate call", extra={"error": str(e)})
        return _response(
            500,
            TriggerResponse(status="error", message=f"Failed to initiate call: {e}"),
        )


def _response(status_code: int, body: TriggerResponse) -> dict[str, Any]:
    """Build a Lambda Function URL response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": body.model_dump_json(),
    }
