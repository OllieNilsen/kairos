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


class TestHandleSuccessfulCall:
    """Tests for _handle_successful_call function."""

    def test_marks_meetings_debriefed(self) -> None:
        """Should mark meetings as debriefed when meeting_ids present."""
        from src.handlers.webhook import _handle_successful_call

        payload = MagicMock()
        payload.variables = {
            "meeting_ids": ["meeting-1", "meeting-2"],
            "user_id": "user-001",
        }
        payload.concatenated_transcript = "Test transcript"

        mock_user_repo = MagicMock()
        mock_user_state = MagicMock(debrief_event_id=None, phone_number="+1234567890")
        mock_user_repo.get_user_state.return_value = mock_user_state

        mock_meetings_repo = MagicMock()
        mock_anthropic = MagicMock()
        mock_anthropic.summarize.return_value = "Summary"
        mock_twilio = MagicMock()
        mock_twilio.send_sms.return_value = "SM123"

        with (
            patch("src.handlers.webhook.get_user_repo", return_value=mock_user_repo),
            patch("src.handlers.webhook.get_meetings_repo", return_value=mock_meetings_repo),
            patch("src.handlers.webhook.get_calendar", return_value=None),
            patch("src.handlers.webhook.get_anthropic", return_value=mock_anthropic),
            patch("src.handlers.webhook.get_twilio", return_value=mock_twilio),
        ):
            result = _handle_successful_call(payload, "user-001")

        mock_meetings_repo.mark_debriefed.assert_called_once_with(
            "user-001", ["meeting-1", "meeting-2"]
        )
        mock_twilio.send_sms.assert_called_once()
        assert result["statusCode"] == 200

    def test_deletes_debrief_calendar_event(self) -> None:
        """Should delete debrief calendar event on success."""
        from src.core.models import UserState
        from src.handlers.webhook import _handle_successful_call

        payload = MagicMock()
        payload.variables = {}
        payload.concatenated_transcript = "Test transcript"

        user_state = UserState(
            user_id="user-001", debrief_event_id="event-123", phone_number="+1234567890"
        )
        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = user_state

        mock_calendar = MagicMock()
        mock_anthropic = MagicMock()
        mock_anthropic.summarize.return_value = "Summary"
        mock_twilio = MagicMock()
        mock_twilio.send_sms.return_value = "SM123"

        with (
            patch("src.handlers.webhook.get_user_repo", return_value=mock_user_repo),
            patch("src.handlers.webhook.get_meetings_repo", return_value=None),
            patch("src.handlers.webhook.get_calendar", return_value=mock_calendar),
            patch("src.handlers.webhook.get_anthropic", return_value=mock_anthropic),
            patch("src.handlers.webhook.get_twilio", return_value=mock_twilio),
        ):
            result = _handle_successful_call(payload, "user-001")

        mock_calendar.delete_event.assert_called_once_with("event-123")
        mock_user_repo.clear_debrief_event.assert_called_once_with("user-001")
        assert result["statusCode"] == 200

    def test_handles_calendar_delete_failure_gracefully(self) -> None:
        """Should continue if calendar event deletion fails."""
        from src.core.models import UserState
        from src.handlers.webhook import _handle_successful_call

        payload = MagicMock()
        payload.variables = {}
        payload.concatenated_transcript = "Test transcript"

        user_state = UserState(
            user_id="user-001", debrief_event_id="event-123", phone_number="+1234567890"
        )
        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = user_state

        mock_calendar = MagicMock()
        mock_calendar.delete_event.side_effect = Exception("Calendar API error")

        mock_anthropic = MagicMock()
        mock_anthropic.summarize.return_value = "Summary"
        mock_twilio = MagicMock()
        mock_twilio.send_sms.return_value = "SM123"

        with (
            patch("src.handlers.webhook.get_user_repo", return_value=mock_user_repo),
            patch("src.handlers.webhook.get_meetings_repo", return_value=None),
            patch("src.handlers.webhook.get_calendar", return_value=mock_calendar),
            patch("src.handlers.webhook.get_anthropic", return_value=mock_anthropic),
            patch("src.handlers.webhook.get_twilio", return_value=mock_twilio),
        ):
            result = _handle_successful_call(payload, "user-001")

        # Should still succeed even if calendar delete fails
        assert result["statusCode"] == 200
        mock_twilio.send_sms.assert_called_once()

    def test_skips_cleanup_when_no_debrief_event(self) -> None:
        """Should skip calendar cleanup when no debrief event exists."""
        from src.core.models import UserState
        from src.handlers.webhook import _handle_successful_call

        payload = MagicMock()
        payload.variables = {}
        payload.concatenated_transcript = "Test transcript"

        user_state = UserState(
            user_id="user-001", debrief_event_id=None, phone_number="+1234567890"
        )
        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = user_state

        mock_calendar = MagicMock()
        mock_anthropic = MagicMock()
        mock_anthropic.summarize.return_value = "Summary"
        mock_twilio = MagicMock()
        mock_twilio.send_sms.return_value = "SM123"

        with (
            patch("src.handlers.webhook.get_user_repo", return_value=mock_user_repo),
            patch("src.handlers.webhook.get_meetings_repo", return_value=None),
            patch("src.handlers.webhook.get_calendar", return_value=mock_calendar),
            patch("src.handlers.webhook.get_anthropic", return_value=mock_anthropic),
            patch("src.handlers.webhook.get_twilio", return_value=mock_twilio),
        ):
            result = _handle_successful_call(payload, "user-001")

        mock_calendar.delete_event.assert_not_called()
        assert result["statusCode"] == 200

    def test_triggers_knowledge_graph_processing(self) -> None:
        """Should trigger transcript saving and entity resolution."""
        from src.core.models import TranscriptTurn
        from src.handlers.webhook import _handle_successful_call

        payload = MagicMock()
        payload.variables = {}
        payload.call_id = "call-123"
        payload.concatenated_transcript = "Test transcript"
        # Mock transcripts from Bland
        transcript_turn = TranscriptTurn(
            id="1", text="Hello", user="user", time="0.0", created_at="2025-01-01T12:00:00Z"
        )
        payload.transcripts = [transcript_turn]

        # Mocks
        mock_transcripts_repo = MagicMock()
        mock_resolution_service = MagicMock()

        # Other mocks needed for successful flow
        mock_user_repo = MagicMock()
        mock_user_state = MagicMock(debrief_event_id=None, phone_number="+1234567890")
        mock_user_repo.get_user_state.return_value = mock_user_state
        mock_anthropic = MagicMock()
        mock_anthropic.summarize.return_value = "Summary"
        mock_twilio = MagicMock()
        mock_twilio.send_sms.return_value = "SM123"

        with (
            patch("src.handlers.webhook.get_user_repo", return_value=mock_user_repo),
            patch("src.handlers.webhook.get_meetings_repo", return_value=None),
            patch("src.handlers.webhook.get_calendar", return_value=None),
            patch("src.handlers.webhook.get_anthropic", return_value=mock_anthropic),
            patch("src.handlers.webhook.get_twilio", return_value=mock_twilio),
            patch("src.handlers.webhook.get_transcripts_repo", return_value=mock_transcripts_repo),
            patch(
                "src.handlers.webhook.get_resolution_service", return_value=mock_resolution_service
            ),
        ):
            result = _handle_successful_call(payload, "user-001")

        # Verify functionality
        mock_transcripts_repo.save_transcript.assert_called_once()
        # Verify arguments: user_id, meeting_id, call_id, segments
        args = mock_transcripts_repo.save_transcript.call_args
        assert args[0][0] == "user-001"  # user_id
        assert args[0][1] == "call-123"  # meeting_id
        assert args[0][2] == "call-123"  # call_id
        assert len(args[0][3]) == 1  # segments

        mock_resolution_service.process_meeting.assert_called_once_with("user-001", "call-123")

        assert result["statusCode"] == 200
