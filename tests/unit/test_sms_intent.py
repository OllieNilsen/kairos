"""Unit tests for SMS intent parsing (LLM-based)."""

import json

import pytest

from src.core.models import SMSIntent
from src.core.sms_intent import (
    INTENT_CLASSIFICATION_PROMPT,
    INTENT_CLASSIFICATION_SYSTEM,
    LLMClient,
    SMSIntentResponse,
    parse_sms_intent,
)


class MockLLMClient:
    """Mock LLM client for testing.

    Returns predetermined responses based on the input prompt.
    """

    def __init__(self, response: str | dict | None = None):
        """Initialize with a fixed response.

        Args:
            response: Either a JSON string, dict (will be serialized), or None
        """
        if isinstance(response, dict):
            self._response = json.dumps(response)
        else:
            self._response = response or '{"intent": "UNKNOWN", "reasoning": "test"}'
        self.last_prompt: str | None = None
        self.last_system: str | None = None
        self.call_count = 0

    def complete(self, prompt: str, system: str = "", max_tokens: int = 100) -> str:
        """Record the call and return the fixed response."""
        self.last_prompt = prompt
        self.last_system = system
        self.call_count += 1
        return self._response


class RaisingLLMClient:
    """Mock LLM client that raises an exception."""

    def complete(self, prompt: str, system: str = "", max_tokens: int = 100) -> str:
        raise RuntimeError("LLM service unavailable")


class TestSMSIntentResponse:
    """Tests for SMSIntentResponse model."""

    def test_valid_response(self):
        """Should parse valid JSON response."""
        response = SMSIntentResponse.model_validate_json(
            '{"intent": "YES", "reasoning": "User said yes"}'
        )
        assert response.intent == "YES"
        assert response.reasoning == "User said yes"

    def test_minimal_response(self):
        """Should accept response without reasoning."""
        response = SMSIntentResponse.model_validate_json('{"intent": "NO"}')
        assert response.intent == "NO"
        assert response.reasoning == ""

    def test_invalid_json_raises(self):
        """Should raise on invalid JSON."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SMSIntentResponse.model_validate_json("not json")


class TestParseIntentWithLLM:
    """Tests for parse_sms_intent with LLM classification."""

    # === Basic intent classification ===

    def test_yes_intent(self):
        """Should return YES when LLM classifies as YES."""
        client = MockLLMClient({"intent": "YES", "reasoning": "User agreed"})
        result = parse_sms_intent("yes please", client)
        assert result == SMSIntent.YES
        assert client.call_count == 1

    def test_ready_intent(self):
        """Should return READY when LLM classifies as READY."""
        client = MockLLMClient({"intent": "READY", "reasoning": "User said ready"})
        result = parse_sms_intent("I'm ready", client)
        assert result == SMSIntent.READY

    def test_no_intent(self):
        """Should return NO when LLM classifies as NO."""
        client = MockLLMClient({"intent": "NO", "reasoning": "User declined"})
        result = parse_sms_intent("not right now", client)
        assert result == SMSIntent.NO

    def test_stop_intent(self):
        """Should return STOP when LLM classifies as STOP."""
        client = MockLLMClient({"intent": "STOP", "reasoning": "User wants to opt out"})
        result = parse_sms_intent("STOP", client)
        assert result == SMSIntent.STOP

    def test_unknown_intent(self):
        """Should return UNKNOWN when LLM cannot classify."""
        client = MockLLMClient({"intent": "UNKNOWN", "reasoning": "Unclear message"})
        result = parse_sms_intent("purple elephant", client)
        assert result == SMSIntent.UNKNOWN

    # === Case insensitivity ===

    def test_lowercase_intent(self):
        """Should handle lowercase intent from LLM."""
        client = MockLLMClient({"intent": "yes", "reasoning": "lowercase"})
        result = parse_sms_intent("sure", client)
        assert result == SMSIntent.YES

    def test_mixed_case_intent(self):
        """Should handle mixed case intent from LLM."""
        client = MockLLMClient({"intent": "No", "reasoning": "mixed"})
        result = parse_sms_intent("nah", client)
        assert result == SMSIntent.NO

    # === Empty/whitespace handling ===

    def test_empty_body_returns_unknown(self):
        """Should return UNKNOWN for empty body without calling LLM."""
        client = MockLLMClient({"intent": "YES"})
        result = parse_sms_intent("", client)
        assert result == SMSIntent.UNKNOWN
        assert client.call_count == 0  # LLM not called

    def test_whitespace_only_returns_unknown(self):
        """Should return UNKNOWN for whitespace-only body."""
        client = MockLLMClient({"intent": "YES"})
        result = parse_sms_intent("   \n\t  ", client)
        assert result == SMSIntent.UNKNOWN
        assert client.call_count == 0

    # === Error handling ===

    def test_llm_error_returns_unknown(self):
        """Should return UNKNOWN when LLM raises exception."""
        client = RaisingLLMClient()
        result = parse_sms_intent("yes", client)
        assert result == SMSIntent.UNKNOWN

    def test_invalid_json_returns_unknown(self):
        """Should return UNKNOWN when LLM returns invalid JSON."""
        client = MockLLMClient("not valid json at all")
        result = parse_sms_intent("yes", client)
        assert result == SMSIntent.UNKNOWN

    def test_missing_intent_field_returns_unknown(self):
        """Should return UNKNOWN when response lacks intent field."""
        client = MockLLMClient('{"reasoning": "no intent field"}')
        result = parse_sms_intent("yes", client)
        assert result == SMSIntent.UNKNOWN

    def test_unrecognized_intent_returns_unknown(self):
        """Should return UNKNOWN for unrecognized intent values."""
        client = MockLLMClient({"intent": "MAYBE", "reasoning": "uncertain"})
        result = parse_sms_intent("perhaps", client)
        assert result == SMSIntent.UNKNOWN

    # === Prompt construction ===

    def test_prompt_includes_message_body(self):
        """Should include the SMS body in the prompt."""
        client = MockLLMClient({"intent": "YES"})
        parse_sms_intent("Hello there", client)
        assert "Hello there" in client.last_prompt

    def test_prompt_uses_system_prompt(self):
        """Should pass the system prompt to LLM."""
        client = MockLLMClient({"intent": "YES"})
        parse_sms_intent("yes", client)
        assert client.last_system == INTENT_CLASSIFICATION_SYSTEM

    def test_body_is_stripped(self):
        """Should strip whitespace from body before including in prompt."""
        client = MockLLMClient({"intent": "YES"})
        parse_sms_intent("  yes please  ", client)
        # The body in the prompt should be stripped
        assert '"  yes please  "' not in client.last_prompt
        assert '"yes please"' in client.last_prompt


class TestPromptContent:
    """Tests for prompt content and structure."""

    def test_system_prompt_describes_intents(self):
        """System prompt should describe all intent types."""
        assert "YES" in INTENT_CLASSIFICATION_SYSTEM
        assert "READY" in INTENT_CLASSIFICATION_SYSTEM
        assert "NO" in INTENT_CLASSIFICATION_SYSTEM
        assert "STOP" in INTENT_CLASSIFICATION_SYSTEM
        assert "UNKNOWN" in INTENT_CLASSIFICATION_SYSTEM

    def test_system_prompt_mentions_opt_out(self):
        """System prompt should clarify STOP is for permanent opt-out."""
        assert "opt out" in INTENT_CLASSIFICATION_SYSTEM.lower()
        assert (
            "permanent" in INTENT_CLASSIFICATION_SYSTEM.lower()
            or "ALL future" in INTENT_CLASSIFICATION_SYSTEM
        )

    def test_prompt_template_has_body_placeholder(self):
        """Prompt template should have placeholder for SMS body."""
        assert "{body}" in INTENT_CLASSIFICATION_PROMPT

    def test_prompt_requests_json_output(self):
        """Prompt should request JSON output."""
        assert "JSON" in INTENT_CLASSIFICATION_PROMPT


class TestLLMClientProtocol:
    """Tests for LLMClient protocol compliance."""

    def test_mock_client_implements_protocol(self):
        """MockLLMClient should implement LLMClient protocol."""
        client: LLMClient = MockLLMClient()
        # Should not raise - protocol is satisfied
        result = client.complete("test", system="sys", max_tokens=50)
        assert isinstance(result, str)

    def test_protocol_requires_complete_method(self):
        """LLMClient protocol requires complete method."""
        # This is a structural check - the protocol defines complete()
        assert hasattr(LLMClient, "complete")
