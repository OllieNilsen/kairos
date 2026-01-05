"""Unit tests for SMS webhook handler."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from src.core.models import SMSIntent, UserState
from src.handlers.sms_webhook import (
    REPLY_ALREADY_CALLED,
    REPLY_NO_MEETINGS,
    REPLY_SNOOZED,
    REPLY_STARTING_CALL,
    REPLY_STOPPED,
    REPLY_UNKNOWN,
    _build_webhook_url,
    _handle_no,
    _handle_ready,
    _handle_stop,
    _twiml_response,
    handler,
)


class TestTwimlResponse:
    """Tests for _twiml_response helper."""

    def test_empty_response(self) -> None:
        """Should return empty TwiML."""
        result = _twiml_response()
        assert result["statusCode"] == 200
        assert result["headers"]["Content-Type"] == "application/xml"
        assert "<Response></Response>" in result["body"]

    def test_with_message(self) -> None:
        """Should include message in TwiML."""
        result = _twiml_response("Hello!")
        assert "<Message>Hello!</Message>" in result["body"]

    def test_custom_status_code(self) -> None:
        """Should use custom status code."""
        result = _twiml_response(status=400)
        assert result["statusCode"] == 400


class TestBuildWebhookUrl:
    """Tests for _build_webhook_url helper."""

    def test_from_request_context(self) -> None:
        """Should build URL from requestContext."""
        event = {
            "requestContext": {
                "domainName": "abc123.lambda-url.eu-west-1.on.aws",
                "http": {"path": "/sms-webhook"},
            }
        }
        result = _build_webhook_url(event)
        assert result == "https://abc123.lambda-url.eu-west-1.on.aws/sms-webhook"

    def test_from_host_header(self) -> None:
        """Should fall back to host header."""
        event = {
            "requestContext": {"http": {"path": "/webhook"}},
            "headers": {"host": "example.com"},
        }
        result = _build_webhook_url(event)
        assert result == "https://example.com/webhook"

    def test_fallback_to_env(self) -> None:
        """Should fall back to environment variable."""
        event = {"requestContext": {}}
        with patch.dict("os.environ", {"SMS_WEBHOOK_URL": "https://fallback.com/sms"}):
            result = _build_webhook_url(event)
            assert result == "https://fallback.com/sms"


class TestHandleNo:
    """Tests for _handle_no intent handler."""

    @patch("src.handlers.sms_webhook.get_user_repo")
    def test_sets_snooze(self, mock_get_repo: MagicMock) -> None:
        """Should set snooze until tomorrow."""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo

        result = _handle_no("user-001")

        # Message is XML-escaped, so check for escaped version
        assert "check in again tomorrow" in result["body"]
        mock_repo.set_snooze.assert_called_once()

        # Check snooze time is tomorrow
        call_args = mock_repo.set_snooze.call_args
        assert call_args[0][0] == "user-001"
        snooze_time = call_args[0][1]
        assert "T06:00:00" in snooze_time  # 6am


class TestHandleStop:
    """Tests for _handle_stop intent handler."""

    @patch("src.handlers.sms_webhook.get_user_repo")
    def test_sets_stop(self, mock_get_repo: MagicMock) -> None:
        """Should set stop flag."""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo

        result = _handle_stop("user-001")

        # Message may be XML-escaped
        assert "unsubscribed" in result["body"]
        mock_repo.set_stop.assert_called_once_with("user-001", stop=True)


class TestHandleReady:
    """Tests for _handle_ready intent handler."""

    @patch("src.handlers.sms_webhook.get_parameter")
    @patch("src.handlers.sms_webhook.BlandClient")
    @patch("src.handlers.sms_webhook.get_meetings_repo")
    @patch("src.handlers.sms_webhook.get_call_dedup")
    @patch("src.handlers.sms_webhook.get_user_repo")
    def test_initiates_call(
        self,
        mock_user_repo: MagicMock,
        mock_call_dedup: MagicMock,
        mock_meetings_repo: MagicMock,
        mock_bland: MagicMock,
        mock_get_param: MagicMock,
    ) -> None:
        """Should initiate a Bland call."""
        # Setup mocks
        mock_call_dedup.return_value.try_initiate_call.return_value = True

        mock_meeting = MagicMock()
        mock_meeting.meeting_id = "meeting-123"
        mock_meeting.title = "Test Meeting"
        mock_meeting.attendees = []
        mock_meeting.attendee_names = []
        mock_meeting.duration_minutes.return_value = 30
        mock_meetings_repo.return_value.get_pending_meetings.return_value = [mock_meeting]

        mock_get_param.return_value = "test-api-key"

        # Create async mock for initiate_call_raw

        async def mock_call(*args: Any, **kwargs: Any) -> str:
            return "call-123"

        mock_bland.return_value.initiate_call_raw = mock_call

        result = _handle_ready("user-001", "+15551234567")

        # Message may be XML-escaped
        assert "Calling you now" in result["body"]
        mock_call_dedup.return_value.try_initiate_call.assert_called_once()

    @patch("src.handlers.sms_webhook.get_call_dedup")
    def test_already_called_today(self, mock_call_dedup: MagicMock) -> None:
        """Should return already-called message if call already made."""
        mock_call_dedup.return_value.try_initiate_call.return_value = False

        result = _handle_ready("user-001", "+15551234567")

        # Message may be XML-escaped
        assert "already in progress" in result["body"]

    @patch("src.handlers.sms_webhook.get_meetings_repo")
    @patch("src.handlers.sms_webhook.get_call_dedup")
    def test_no_meetings(self, mock_call_dedup: MagicMock, mock_meetings_repo: MagicMock) -> None:
        """Should return no-meetings message if nothing to debrief."""
        mock_call_dedup.return_value.try_initiate_call.return_value = True
        mock_meetings_repo.return_value.get_pending_meetings.return_value = []

        result = _handle_ready("user-001", "+15551234567")

        # Message may be XML-escaped
        assert "No meetings to debrief" in result["body"]
        # Should release the call lock
        mock_call_dedup.return_value.release_call.assert_called_once()


class TestHandler:
    """Tests for the main handler function."""

    def _make_event(
        self,
        body: str = "Body=Yes&From=%2B15551234567&To=%2B447700900123&AccountSid=AC123&MessageSid=SM123",
        signature: str = "valid-sig",
    ) -> dict[str, Any]:
        """Create a mock Lambda event."""
        return {
            "body": body,
            "headers": {"x-twilio-signature": signature},
            "requestContext": {
                "domainName": "test.lambda-url.eu-west-1.on.aws",
                "http": {"path": "/sms-webhook"},
            },
            "isBase64Encoded": False,
        }

    @patch("src.handlers.sms_webhook.get_llm_client")
    @patch("src.handlers.sms_webhook.get_user_repo")
    @patch("src.handlers.sms_webhook.get_inbound_dedup")
    @patch("src.handlers.sms_webhook.verify_twilio_signature")
    @patch("src.handlers.sms_webhook.get_parameter")
    def test_parses_and_dedupes(
        self,
        mock_param: MagicMock,
        mock_verify: MagicMock,
        mock_dedup: MagicMock,
        mock_user_repo: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """Should parse SMS and check idempotency."""
        mock_param.return_value = "auth-token"
        mock_verify.return_value = True
        mock_dedup.return_value.try_process_message.return_value = False  # Duplicate

        event = self._make_event()

        with patch.dict("os.environ", {"SSM_TWILIO_AUTH_TOKEN": "/kairos/test"}):
            result = handler(event, MagicMock())

        # Should return empty TwiML for duplicate
        assert result["statusCode"] == 200
        assert "<Response></Response>" in result["body"]

    @patch("src.handlers.sms_webhook.parse_sms_intent")
    @patch("src.handlers.sms_webhook.get_llm_client")
    @patch("src.handlers.sms_webhook.get_users_repo")
    @patch("src.handlers.sms_webhook.get_user_repo")
    @patch("src.handlers.sms_webhook.get_inbound_dedup")
    @patch("src.handlers.sms_webhook.verify_twilio_signature")
    @patch("src.handlers.sms_webhook.get_parameter")
    def test_handles_unknown_intent(
        self,
        mock_param: MagicMock,
        mock_verify: MagicMock,
        mock_dedup: MagicMock,
        mock_user_repo: MagicMock,
        mock_users_repo: MagicMock,
        mock_llm: MagicMock,
        mock_parse: MagicMock,
    ) -> None:
        """Should return clarification for unknown intent."""
        mock_param.return_value = "auth-token"
        mock_verify.return_value = True
        mock_dedup.return_value.try_process_message.return_value = True
        mock_users_repo.return_value.get_user_by_phone.return_value = "user-001"
        mock_user_repo.return_value.get_user_state.return_value = UserState(
            user_id="user-001", phone_number="+15551234567"
        )
        mock_parse.return_value = SMSIntent.UNKNOWN

        event = self._make_event(
            body="Body=purple%20elephant&From=%2B15551234567&To=%2B447700900123&AccountSid=AC123&MessageSid=SM123"
        )

        with patch.dict("os.environ", {"SSM_TWILIO_AUTH_TOKEN": "/kairos/test"}):
            result = handler(event, MagicMock())

        # Message may be XML-escaped
        assert "didn" in result["body"]  # "I didn't understand that"

    def test_missing_message_sid(self) -> None:
        """Should return 400 for missing MessageSid."""
        event = self._make_event(body="Body=Hello&From=%2B15551234567")

        result = handler(event, MagicMock())

        assert result["statusCode"] == 400

    @patch("src.handlers.sms_webhook.verify_twilio_signature")
    @patch("src.handlers.sms_webhook.get_parameter")
    def test_invalid_signature(self, mock_param: MagicMock, mock_verify: MagicMock) -> None:
        """Should return 401 for invalid signature."""
        mock_param.return_value = "auth-token"
        mock_verify.return_value = False

        event = self._make_event()

        with patch.dict("os.environ", {"SSM_TWILIO_AUTH_TOKEN": "/kairos/test"}):
            result = handler(event, MagicMock())

        assert result["statusCode"] == 401

    @patch("src.handlers.sms_webhook._handle_no")
    @patch("src.handlers.sms_webhook.parse_sms_intent")
    @patch("src.handlers.sms_webhook.get_llm_client")
    @patch("src.handlers.sms_webhook.get_users_repo")
    @patch("src.handlers.sms_webhook.get_user_repo")
    @patch("src.handlers.sms_webhook.get_inbound_dedup")
    @patch("src.handlers.sms_webhook.verify_twilio_signature")
    @patch("src.handlers.sms_webhook.get_parameter")
    def test_routes_no_intent(
        self,
        mock_param: MagicMock,
        mock_verify: MagicMock,
        mock_dedup: MagicMock,
        mock_user_repo: MagicMock,
        mock_users_repo: MagicMock,
        mock_llm: MagicMock,
        mock_parse: MagicMock,
        mock_handle_no: MagicMock,
    ) -> None:
        """Should route NO intent to handler."""
        mock_param.return_value = "auth-token"
        mock_verify.return_value = True
        mock_dedup.return_value.try_process_message.return_value = True
        mock_users_repo.return_value.get_user_by_phone.return_value = "user-001"
        mock_user_repo.return_value.get_user_state.return_value = UserState(
            user_id="user-001", phone_number="+15551234567"
        )
        mock_parse.return_value = SMSIntent.NO
        mock_handle_no.return_value = _twiml_response(REPLY_SNOOZED)

        event = self._make_event()

        with patch.dict("os.environ", {"SSM_TWILIO_AUTH_TOKEN": "/kairos/test"}):
            handler(event, MagicMock())

        mock_handle_no.assert_called_once_with("user-001")

    @patch("src.handlers.sms_webhook._handle_stop")
    @patch("src.handlers.sms_webhook.parse_sms_intent")
    @patch("src.handlers.sms_webhook.get_llm_client")
    @patch("src.handlers.sms_webhook.get_users_repo")
    @patch("src.handlers.sms_webhook.get_user_repo")
    @patch("src.handlers.sms_webhook.get_inbound_dedup")
    @patch("src.handlers.sms_webhook.verify_twilio_signature")
    @patch("src.handlers.sms_webhook.get_parameter")
    def test_routes_stop_intent(
        self,
        mock_param: MagicMock,
        mock_verify: MagicMock,
        mock_dedup: MagicMock,
        mock_user_repo: MagicMock,
        mock_users_repo: MagicMock,
        mock_llm: MagicMock,
        mock_parse: MagicMock,
        mock_handle_stop: MagicMock,
    ) -> None:
        """Should route STOP intent to handler."""
        mock_param.return_value = "auth-token"
        mock_verify.return_value = True
        mock_dedup.return_value.try_process_message.return_value = True
        mock_users_repo.return_value.get_user_by_phone.return_value = "user-001"
        mock_user_repo.return_value.get_user_state.return_value = UserState(
            user_id="user-001", phone_number="+15551234567"
        )
        mock_parse.return_value = SMSIntent.STOP
        mock_handle_stop.return_value = _twiml_response(REPLY_STOPPED)

        event = self._make_event()

        with patch.dict("os.environ", {"SSM_TWILIO_AUTH_TOKEN": "/kairos/test"}):
            handler(event, MagicMock())

        mock_handle_stop.assert_called_once_with("user-001")


class TestReplyMessages:
    """Tests for reply message constants."""

    def test_messages_are_not_empty(self) -> None:
        """All reply messages should be non-empty."""
        messages = [
            REPLY_STARTING_CALL,
            REPLY_SNOOZED,
            REPLY_STOPPED,
            REPLY_UNKNOWN,
            REPLY_NO_MEETINGS,
            REPLY_ALREADY_CALLED,
        ]
        for msg in messages:
            assert msg
            assert len(msg) > 10

    def test_unknown_message_is_helpful(self) -> None:
        """Unknown message should guide user on what to reply."""
        assert "YES" in REPLY_UNKNOWN or "yes" in REPLY_UNKNOWN.lower()
        assert "NO" in REPLY_UNKNOWN or "no" in REPLY_UNKNOWN.lower()


class TestMultiUserPhoneRouting:
    """Tests for multi-user phone routing (Slice 4B)."""

    @staticmethod
    def _build_sms_event(phone_number: str = "+442012341234") -> dict[str, Any]:
        """Build a test SMS webhook event."""
        import urllib.parse

        # URL-encode the phone numbers (+ becomes %2B)
        from_encoded = urllib.parse.quote(phone_number, safe="")
        to_encoded = urllib.parse.quote("+441234567890", safe="")

        return {
            "body": (
                f"Body=YES&From={from_encoded}&To={to_encoded}&"
                "MessageSid=SM123abc&AccountSid=ACabc123"
            ),
            "headers": {"x-twilio-signature": "fake_signature"},
            "requestContext": {
                "http": {"path": "/sms-webhook"},
                "domainName": "test.lambda-url.eu-west-1.on.aws",
            },
        }

    def test_phone_routing_lookup_success(self) -> None:
        """Should lookup user_id by phone number using UsersRepository."""
        from src.handlers.sms_webhook import handler

        mock_users_repo = MagicMock()
        mock_users_repo.get_user_by_phone.return_value = "user-123"

        mock_user_state_repo = MagicMock()
        mock_user_state = UserState(
            user_id="user-123",
            phone_number="+442012341234",
            awaiting_reply=True,
        )
        mock_user_state_repo.get_user_state.return_value = mock_user_state

        with (
            patch("src.handlers.sms_webhook.get_users_repo", return_value=mock_users_repo),
            patch("src.handlers.sms_webhook.get_user_repo", return_value=mock_user_state_repo),
            patch("src.handlers.sms_webhook.get_inbound_dedup") as mock_dedup,
            patch("src.handlers.sms_webhook.get_parameter", return_value="fake_token"),
            patch("src.handlers.sms_webhook.verify_twilio_signature", return_value=True),
            patch("src.handlers.sms_webhook._handle_ready") as mock_handle_ready,
        ):
            mock_dedup_instance = MagicMock()
            mock_dedup_instance.is_duplicate.return_value = False
            mock_dedup.return_value = mock_dedup_instance
            mock_handle_ready.return_value = {"statusCode": 200, "body": "OK"}

            event = self._build_sms_event("+442012341234")
            result = handler(event, MagicMock())

            # Verify phone lookup was called
            mock_users_repo.get_user_by_phone.assert_called_once_with(
                "+442012341234", enforce_rate_limit=True
            )

            # Verify user state lookup used the routed user_id
            mock_user_state_repo.get_user_state.assert_called_once_with("user-123")

            assert result["statusCode"] == 200

    def test_phone_not_registered_returns_error(self) -> None:
        """Should return error message if phone number is not registered."""
        from src.handlers.sms_webhook import handler

        mock_users_repo = MagicMock()
        mock_users_repo.get_user_by_phone.return_value = None  # Not registered

        with (
            patch("src.handlers.sms_webhook.get_users_repo", return_value=mock_users_repo),
            patch("src.handlers.sms_webhook.get_inbound_dedup") as mock_dedup,
            patch("src.handlers.sms_webhook.get_parameter", return_value="fake_token"),
            patch("src.handlers.sms_webhook.verify_twilio_signature", return_value=True),
        ):
            mock_dedup_instance = MagicMock()
            mock_dedup_instance.is_duplicate.return_value = False
            mock_dedup.return_value = mock_dedup_instance

            event = self._build_sms_event("+449999999999")
            result = handler(event, MagicMock())

            # Should return error
            assert result["statusCode"] == 200  # TwiML always 200
            assert "not registered" in result["body"].lower()

    def test_phone_enumeration_rate_limit_enforced(self) -> None:
        """Should enforce rate limit on phone lookups (security - P0)."""
        from src.adapters.users_repo import PhoneEnumerationRateLimitError
        from src.handlers.sms_webhook import handler

        mock_users_repo = MagicMock()
        mock_users_repo.get_user_by_phone.side_effect = PhoneEnumerationRateLimitError(
            "Rate limit exceeded"
        )

        with (
            patch("src.handlers.sms_webhook.get_users_repo", return_value=mock_users_repo),
            patch("src.handlers.sms_webhook.get_inbound_dedup") as mock_dedup,
            patch("src.handlers.sms_webhook.get_parameter", return_value="fake_token"),
            patch("src.handlers.sms_webhook.verify_twilio_signature", return_value=True),
        ):
            mock_dedup_instance = MagicMock()
            mock_dedup_instance.is_duplicate.return_value = False
            mock_dedup.return_value = mock_dedup_instance

            event = self._build_sms_event("+449999999999")
            result = handler(event, MagicMock())

            # Should reject with rate limit error
            assert result["statusCode"] == 429

    def test_multi_user_isolation(self) -> None:
        """Should route different phone numbers to different users (isolation - P0)."""
        from src.handlers.sms_webhook import handler

        # User 1
        mock_users_repo_1 = MagicMock()
        mock_users_repo_1.get_user_by_phone.return_value = "user-001"

        mock_user_state_repo_1 = MagicMock()
        mock_user_state_1 = UserState(
            user_id="user-001",
            phone_number="+441234567890",
            awaiting_reply=True,
        )
        mock_user_state_repo_1.get_user_state.return_value = mock_user_state_1

        with (
            patch("src.handlers.sms_webhook.get_users_repo", return_value=mock_users_repo_1),
            patch("src.handlers.sms_webhook.get_user_repo", return_value=mock_user_state_repo_1),
            patch("src.handlers.sms_webhook.get_inbound_dedup") as mock_dedup,
            patch("src.handlers.sms_webhook.get_parameter", return_value="fake_token"),
            patch("src.handlers.sms_webhook.verify_twilio_signature", return_value=True),
            patch("src.handlers.sms_webhook._handle_ready", return_value={"statusCode": 200}),
        ):
            mock_dedup_instance = MagicMock()
            mock_dedup_instance.is_duplicate.return_value = False
            mock_dedup.return_value = mock_dedup_instance

            event_1 = self._build_sms_event("+441234567890")
            handler(event_1, MagicMock())

            # Verify user-001 state was accessed
            mock_user_state_repo_1.get_user_state.assert_called_with("user-001")

        # User 2 (different phone)
        mock_users_repo_2 = MagicMock()
        mock_users_repo_2.get_user_by_phone.return_value = "user-002"

        mock_user_state_repo_2 = MagicMock()
        mock_user_state_2 = UserState(
            user_id="user-002",
            phone_number="+449876543210",
            awaiting_reply=True,
        )
        mock_user_state_repo_2.get_user_state.return_value = mock_user_state_2

        with (
            patch("src.handlers.sms_webhook.get_users_repo", return_value=mock_users_repo_2),
            patch("src.handlers.sms_webhook.get_user_repo", return_value=mock_user_state_repo_2),
            patch("src.handlers.sms_webhook.get_inbound_dedup") as mock_dedup,
            patch("src.handlers.sms_webhook.get_parameter", return_value="fake_token"),
            patch("src.handlers.sms_webhook.verify_twilio_signature", return_value=True),
            patch("src.handlers.sms_webhook._handle_ready", return_value={"statusCode": 200}),
        ):
            mock_dedup_instance = MagicMock()
            mock_dedup_instance.is_duplicate.return_value = False
            mock_dedup.return_value = mock_dedup_instance

            event_2 = self._build_sms_event("+449876543210")
            handler(event_2, MagicMock())

            # Verify user-002 state was accessed (not user-001)
            mock_user_state_repo_2.get_user_state.assert_called_with("user-002")
