"""SMS intent parsing for inbound Twilio messages.

Uses LLM-based classification (AI-first) instead of brittle keyword matching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

# Support both Lambda and test import paths
try:
    from core.models import SMSIntent
except ImportError:
    from src.core.models import SMSIntent

if TYPE_CHECKING:
    # Use the canonical LLMClient protocol from interfaces
    try:
        from core.interfaces import LLMClient
    except ImportError:
        from src.core.interfaces import LLMClient


class SMSIntentResponse(BaseModel):
    """Structured response from LLM intent classification."""

    intent: str = Field(description="One of: YES, READY, NO, STOP, UNKNOWN")
    reasoning: str = Field(default="", description="Brief explanation")


# System prompt for intent classification
INTENT_CLASSIFICATION_SYSTEM = """You are a classifier for SMS replies to a daily debrief prompt.

The user received a message asking if they want a debrief call. Classify their reply.

Available intents:
- YES: User agrees to start the call (yes, yeah, ok, sure, call me, 👍, etc.)
- READY: User explicitly says they're ready (ready, i'm ready, available now)
- NO: User declines or wants to skip (no, not now, busy, later, skip, tomorrow)
- STOP: User wants to opt out of ALL future messages (stop, unsubscribe, cancel)
- UNKNOWN: Cannot determine intent from the message

Important:
- STOP is for permanent opt-out, not just declining today
- YES and READY are similar; use READY only if they explicitly say "ready"
- Be lenient - interpret casual affirmations as YES
- Emoji responses: 👍👌✅ = YES, 👎❌ = NO"""

INTENT_CLASSIFICATION_PROMPT = """Classify this SMS reply:

"{body}"

Respond with ONLY valid JSON (no markdown):
{{"intent": "<YES|READY|NO|STOP|UNKNOWN>", "reasoning": "<brief explanation>"}}"""


def parse_sms_intent(body: str, llm_client: LLMClient) -> SMSIntent:
    """Parse user intent from SMS message body using LLM classification.

    Uses AI-first approach: LLM classifies the intent instead of brittle
    keyword matching. Returns structured output validated by Pydantic.

    Args:
        body: Raw SMS message body
        llm_client: LLM client implementing the LLMClient protocol

    Returns:
        Parsed SMSIntent enum value

    Examples:
        >>> parse_sms_intent("Yes please", llm_client)
        SMSIntent.YES
        >>> parse_sms_intent("Not right now", llm_client)
        SMSIntent.NO
        >>> parse_sms_intent("STOP", llm_client)
        SMSIntent.STOP
    """
    if not body or not body.strip():
        return SMSIntent.UNKNOWN

    # Build the prompt
    prompt = INTENT_CLASSIFICATION_PROMPT.format(body=body.strip())

    try:
        # Call LLM for classification
        response = llm_client.complete(
            prompt=prompt,
            system_prompt=INTENT_CLASSIFICATION_SYSTEM,
        )

        # Parse and validate the response
        result = SMSIntentResponse.model_validate_json(response)

        # Map to enum (case-insensitive)
        intent_map = {
            "YES": SMSIntent.YES,
            "READY": SMSIntent.READY,
            "NO": SMSIntent.NO,
            "STOP": SMSIntent.STOP,
        }
        return intent_map.get(result.intent.upper(), SMSIntent.UNKNOWN)

    except Exception:
        # On any parsing/LLM error, return UNKNOWN (fail safe)
        return SMSIntent.UNKNOWN
