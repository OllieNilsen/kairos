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

    def test_invalid_phone_format(self):
        """Invalid phone number should raise validation error."""
        with pytest.raises(ValidationError) as exc_info:
            TriggerPayload(
                phone_number="555-123-4567",  # Wrong format
                event_context=EventContext(
                    event_type="meeting_debrief",
                    subject="Test",
                ),
                interview_prompts=["Question?"],
            )
        assert "phone_number" in str(exc_info.value)

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
            duration=180,
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
                "duration": 60,
            }
        )
        assert payload.from_number == "+18001234567"
