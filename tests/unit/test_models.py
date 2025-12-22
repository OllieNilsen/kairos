"""Unit tests for Pydantic models."""

import pytest
from pydantic import ValidationError

from src.core.models import (
    BlandWebhookPayload,
    EventContext,
    TranscriptTurn,
    TriggerPayload,
)


class TestTriggerPayload:
    """Tests for TriggerPayload validation."""

    def test_valid_payload(self):
        """Valid payload should parse correctly."""
        payload = TriggerPayload(
            phone_number="+15551234567",
            event_context=EventContext(
                event_type="meeting_debrief",
                subject="Q4 Planning",
                participants=["Sarah", "Mike"],
            ),
            interview_prompts=["What was discussed?"],
        )
        assert payload.phone_number == "+15551234567"
        assert payload.event_context.subject == "Q4 Planning"

    # === Phone Number E.164 Validation Tests ===

    @pytest.mark.parametrize(
        "phone",
        [
            "+15551234567",  # US
            "+447584019464",  # UK
            "+33612345678",  # France
            "+491701234567",  # Germany
            "+81901234567",  # Japan
            "+8613812345678",  # China (14 digits)
            "+1234567",  # Minimum 7 digits
            "+123456789012345",  # Maximum 15 digits
        ],
    )
    def test_valid_international_phone_numbers(self, phone: str):
        """E.164 international phone numbers should be accepted."""
        payload = TriggerPayload(
            phone_number=phone,
            event_context=EventContext(event_type="general", subject="Test"),
            interview_prompts=["Question?"],
        )
        assert payload.phone_number == phone

    @pytest.mark.parametrize(
        "phone,reason",
        [
            ("555-123-4567", "no country code"),
            ("5551234567", "missing + prefix"),
            ("+0123456789", "starts with 0 after +"),
            ("+123456", "too short (6 digits)"),
            ("+1234567890123456", "too long (16 digits)"),
            ("++15551234567", "double +"),
            ("+1-555-123-4567", "contains dashes"),
            ("+1 555 123 4567", "contains spaces"),
            ("+1(555)1234567", "contains parentheses"),
            ("", "empty string"),
        ],
    )
    def test_invalid_phone_formats(self, phone: str, reason: str):
        """Invalid phone formats should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TriggerPayload(
                phone_number=phone,
                event_context=EventContext(event_type="general", subject="Test"),
                interview_prompts=["Question?"],
            )
        assert "phone_number" in str(exc_info.value), f"Failed for: {reason}"

    def test_empty_prompts_rejected(self):
        """At least one prompt is required."""
        with pytest.raises(ValidationError):
            TriggerPayload(
                phone_number="+15551234567",
                event_context=EventContext(
                    event_type="meeting_debrief",
                    subject="Test",
                ),
                interview_prompts=[],  # Empty list
            )


class TestBlandWebhookPayload:
    """Tests for BlandWebhookPayload validation."""

    def test_valid_webhook(self):
        """Valid webhook payload should parse correctly."""
        payload = BlandWebhookPayload(
            call_id="abc-123",
            status="completed",
            to="+15551234567",
            **{"from": "+18005551234"},  # 'from' is a reserved keyword
            call_length=3.5,
            transcript=[
                TranscriptTurn(speaker="assistant", text="Hello"),
                TranscriptTurn(speaker="user", text="Hi there"),
            ],
            concatenated_transcript="Assistant: Hello\nUser: Hi there",
        )
        assert payload.call_id == "abc-123"
        assert payload.from_number == "+18005551234"
        assert len(payload.transcript) == 2

    def test_from_alias(self):
        """'from' field should be accessible via from_number."""
        payload = BlandWebhookPayload.model_validate(
            {
                "call_id": "test",
                "status": "completed",
                "to": "+15551234567",
                "from": "+18001234567",
            }
        )
        assert payload.from_number == "+18001234567"

    def test_ignores_extra_fields(self):
        """Should ignore unknown fields from Bland API."""
        payload = BlandWebhookPayload.model_validate(
            {
                "call_id": "test",
                "status": "completed",
                "unknown_field": "ignored",
                "transfer_duration": None,
                "another_field": {"nested": "value"},
            }
        )
        assert payload.call_id == "test"

    def test_nested_variables(self):
        """Should accept nested metadata in variables."""
        payload = BlandWebhookPayload.model_validate(
            {
                "call_id": "test",
                "status": "completed",
                "variables": {
                    "metadata": {
                        "event_context": '{"event_type": "general", "subject": "Test"}'
                    }
                },
            }
        )
        assert payload.variables["metadata"]["event_context"] is not None
