"""Unit tests for trigger handler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTriggerHandler:
    """Tests for trigger Lambda handler."""

    @pytest.fixture
    def mock_env(self) -> dict[str, str]:
        """Environment variables for testing."""
        return {
            "SSM_BLAND_API_KEY": "/kairos/bland-api-key",
            "WEBHOOK_URL": "https://example.com/webhook",
        }

    @pytest.fixture
    def valid_payload(self) -> dict:
        """Create a valid trigger payload."""
        return {
            "phone_number": "+447700900000",
            "event_context": {
                "event_type": "meeting_debrief",
                "subject": "Team Standup",
                "participants": ["Alice", "Bob"],
            },
            "interview_prompts": ["What was discussed?", "Any action items?"],
        }

    def test_invalid_json_returns_400(self, mock_env: dict[str, str]) -> None:
        """Should return 400 for invalid JSON."""
        from src.handlers.trigger import handler

        event = {"body": "not-valid-json{"}

        with patch.dict("os.environ", mock_env):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["status"] == "error"

    def test_invalid_payload_returns_400(self, mock_env: dict[str, str]) -> None:
        """Should return 400 for invalid payload structure."""
        from src.handlers.trigger import handler

        event = {"body": json.dumps({"invalid": "payload"})}

        with patch.dict("os.environ", mock_env):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 400

    def test_successful_call_returns_202(
        self, mock_env: dict[str, str], valid_payload: dict
    ) -> None:
        """Should return 202 when call is initiated."""
        from src.handlers.trigger import handler

        event = {"body": json.dumps(valid_payload)}

        mock_bland = MagicMock()
        mock_bland.initiate_call = AsyncMock(return_value="call-123")

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.trigger.get_bland_client", return_value=mock_bland),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 202
        body = json.loads(response["body"])
        assert body["status"] == "initiated"
        assert body["call_id"] == "call-123"

    def test_call_failure_returns_500(self, mock_env: dict[str, str], valid_payload: dict) -> None:
        """Should return 500 when call initiation fails."""
        from src.handlers.trigger import handler

        event = {"body": json.dumps(valid_payload)}

        mock_bland = MagicMock()
        mock_bland.initiate_call = AsyncMock(side_effect=Exception("API error"))

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.trigger.get_bland_client", return_value=mock_bland),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["status"] == "error"

    def test_handles_string_body(self, mock_env: dict[str, str], valid_payload: dict) -> None:
        """Should handle body as string."""
        from src.handlers.trigger import handler

        event = {"body": json.dumps(valid_payload)}

        mock_bland = MagicMock()
        mock_bland.initiate_call = AsyncMock(return_value="call-123")

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.trigger.get_bland_client", return_value=mock_bland),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 202

    def test_handles_dict_body(self, mock_env: dict[str, str], valid_payload: dict) -> None:
        """Should handle body as dict (API Gateway v2)."""
        from src.handlers.trigger import handler

        event = {"body": valid_payload}  # Already a dict

        mock_bland = MagicMock()
        mock_bland.initiate_call = AsyncMock(return_value="call-123")

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.trigger.get_bland_client", return_value=mock_bland),
        ):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 202


class TestGetBlandClient:
    """Tests for get_bland_client function."""

    def test_creates_client_from_ssm(self) -> None:
        """Should create client with API key from SSM."""
        from src.handlers import trigger

        # Reset cached client
        trigger._bland_client = None

        with (
            patch.dict("os.environ", {"SSM_BLAND_API_KEY": "/kairos/bland-key"}),
            patch("src.handlers.trigger.get_parameter", return_value="test-api-key"),
            patch("src.handlers.trigger.BlandClient") as mock_client,
        ):
            trigger.get_bland_client()

        mock_client.assert_called_once_with("test-api-key")

    def test_reuses_cached_client(self) -> None:
        """Should reuse cached client on subsequent calls."""
        from src.handlers import trigger

        mock_client = MagicMock()
        trigger._bland_client = mock_client

        result = trigger.get_bland_client()

        assert result is mock_client

        # Reset for other tests
        trigger._bland_client = None


class TestResponseHelper:
    """Tests for _response helper function."""

    def test_formats_response_correctly(self) -> None:
        """Should format Lambda response correctly."""
        from src.core.models import TriggerResponse
        from src.handlers.trigger import _response

        body = TriggerResponse(status="initiated", call_id="call-123", message="Test message")
        result = _response(200, body)

        assert result["statusCode"] == 200
        assert result["headers"]["Content-Type"] == "application/json"
        parsed = json.loads(result["body"])
        assert parsed["status"] == "initiated"
        assert parsed["message"] == "Test message"
