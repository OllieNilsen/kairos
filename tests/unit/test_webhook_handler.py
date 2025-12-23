"""Unit tests for webhook handler."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestIsCallSuccessful:
    """Tests for _is_call_successful function."""

    @pytest.fixture
    def mock_payload(self) -> MagicMock:
        """Create a mock webhook payload."""
        payload = MagicMock()
        payload.status = "completed"
        payload.call_length = 2.0  # 2 minutes
        payload.concatenated_transcript = "Hello, how was your meeting today?"
        return payload

    def test_successful_call(self, mock_payload: MagicMock) -> None:
        """Should return True for successful call."""
        from src.handlers.webhook import _is_call_successful

        assert _is_call_successful(mock_payload) is True

    def test_non_completed_status(self, mock_payload: MagicMock) -> None:
        """Should return False for non-completed status."""
        from src.handlers.webhook import _is_call_successful

        mock_payload.status = "failed"
        assert _is_call_successful(mock_payload) is False

    def test_short_call(self, mock_payload: MagicMock) -> None:
        """Should return False for calls under 30 seconds."""
        from src.handlers.webhook import _is_call_successful

        mock_payload.call_length = 0.3  # 18 seconds
        assert _is_call_successful(mock_payload) is False

    def test_voicemail_detected(self, mock_payload: MagicMock) -> None:
        """Should return False when voicemail keywords detected."""
        from src.handlers.webhook import _is_call_successful

        mock_payload.concatenated_transcript = "Please leave a message after the beep"
        assert _is_call_successful(mock_payload) is False

    def test_voicemail_keywords(self, mock_payload: MagicMock) -> None:
        """Should detect various voicemail keywords."""
        from src.handlers.webhook import _is_call_successful

        voicemail_phrases = [
            "Hi, you've reached my voicemail",
            "Please leave your message",
            "I'm not available right now",
            "After the tone, please leave",
            "Welcome to the mailbox of",
        ]

        for phrase in voicemail_phrases:
            mock_payload.concatenated_transcript = phrase
            assert _is_call_successful(mock_payload) is False, f"Should detect: {phrase}"


class TestExtractEventContext:
    """Tests for _extract_event_context function."""

    def test_extracts_from_nested_metadata(self) -> None:
        """Should extract context from nested metadata path."""
        from src.handlers.webhook import _extract_event_context

        payload = MagicMock()
        payload.variables = {
            "metadata": {
                "event_context": '{"event_type":"meeting_debrief","subject":"Standup","participants":["Alice"]}'
            }
        }

        context = _extract_event_context(payload)

        assert context.event_type == "meeting_debrief"
        assert context.subject == "Standup"
        assert context.participants == ["Alice"]

    def test_extracts_from_flat_path(self) -> None:
        """Should extract context from flat variables path."""
        from src.handlers.webhook import _extract_event_context

        payload = MagicMock()
        payload.variables = {
            "event_context": {"event_type": "general", "subject": "Call", "participants": []}
        }

        context = _extract_event_context(payload)

        assert context.event_type == "general"
        assert context.subject == "Call"

    def test_returns_default_on_missing(self) -> None:
        """Should return default context when missing."""
        from src.handlers.webhook import _extract_event_context

        payload = MagicMock()
        payload.variables = {}

        context = _extract_event_context(payload)

        assert context.event_type == "general"
        assert context.subject == "Debrief Call"
        assert context.participants == []

    def test_returns_default_on_invalid_json(self) -> None:
        """Should return default context on invalid JSON."""
        from src.handlers.webhook import _extract_event_context

        payload = MagicMock()
        payload.variables = {"metadata": {"event_context": "not-valid-json"}}

        context = _extract_event_context(payload)

        assert context.event_type == "general"


class TestWebhookHandler:
    """Tests for the main handler function."""

    @pytest.fixture
    def mock_env(self) -> dict[str, str]:
        """Environment variables for testing."""
        return {
            "SSM_ANTHROPIC_API_KEY": "/kairos/anthropic-api-key",
            "SSM_BLAND_WEBHOOK_SECRET": "/kairos/webhook-secret",
            "SENDER_EMAIL": "sender@example.com",
            "RECIPIENT_EMAIL": "recipient@example.com",
            "DEDUP_TABLE_NAME": "kairos-dedup",
            "USER_STATE_TABLE": "kairos-user-state",
            "IDEMPOTENCY_TABLE": "kairos-idempotency",
        }

    def test_invalid_signature_returns_401(self, mock_env: dict[str, str]) -> None:
        """Should return 401 for invalid webhook signature."""
        from src.handlers.webhook import handler

        event = {
            "body": '{"call_id": "test-123"}',
            "headers": {"x-webhook-signature": "invalid"},
        }

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.webhook.get_parameter", return_value="secret"),
            patch("src.handlers.webhook.verify_bland_signature", return_value=False),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 401

    def test_invalid_payload_returns_400(self, mock_env: dict[str, str]) -> None:
        """Should return 400 for invalid payload."""
        from src.handlers.webhook import handler

        event = {
            "body": '{"invalid": "payload"}',
            "headers": {},
        }

        # Remove webhook secret so signature check is skipped
        env_without_secret = {k: v for k, v in mock_env.items() if k != "SSM_BLAND_WEBHOOK_SECRET"}

        with patch.dict("os.environ", env_without_secret, clear=True):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 400

    def test_duplicate_call_returns_200(self, mock_env: dict[str, str]) -> None:
        """Should return 200 for duplicate call_id."""
        from src.handlers.webhook import handler

        valid_payload = {
            "call_id": "test-123",
            "status": "completed",
            "call_length": 2.0,
            "concatenated_transcript": "Hello",
            "variables": {},
        }

        event = {
            "body": json.dumps(valid_payload),
            "headers": {},
        }

        env_without_secret = {k: v for k, v in mock_env.items() if k != "SSM_BLAND_WEBHOOK_SECRET"}

        mock_dedup = MagicMock()
        mock_dedup.is_duplicate.return_value = True

        with (
            patch.dict("os.environ", env_without_secret, clear=True),
            patch("src.handlers.webhook.get_deduplicator", return_value=mock_dedup),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["status"] == "duplicate"
