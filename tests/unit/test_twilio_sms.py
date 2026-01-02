"""Unit tests for Twilio SMS adapter."""

import hashlib
import hmac
from base64 import b64encode
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.twilio_sms import (
    TwilioClient,
    build_twiml_response,
    parse_twilio_webhook_body,
    verify_twilio_signature,
)


class TestTwilioClient:
    """Tests for TwilioClient."""

    @pytest.fixture
    def client(self) -> TwilioClient:
        """Create a test Twilio client."""
        return TwilioClient(
            account_sid="AC1234567890abcdef",
            auth_token="test_auth_token",
            from_number="+15551234567",
        )

    def test_init(self, client: TwilioClient) -> None:
        """Should initialize with credentials."""
        assert client.account_sid == "AC1234567890abcdef"
        assert client.auth_token == "test_auth_token"
        assert client.from_number == "+15551234567"

    @patch("httpx.post")
    def test_send_sms_success(self, mock_post: MagicMock, client: TwilioClient) -> None:
        """Should send SMS and return message SID."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "sid": "SM1234567890abcdef",
            "status": "queued",
        }
        mock_post.return_value = mock_response

        result = client.send_sms("+447700900123", "Hello from Kairos!")

        assert result == "SM1234567890abcdef"
        mock_post.assert_called_once()

        # Verify the request
        call_args = mock_post.call_args
        assert "Messages.json" in call_args[0][0]
        assert call_args[1]["auth"] == ("AC1234567890abcdef", "test_auth_token")
        assert call_args[1]["data"]["To"] == "+447700900123"
        assert call_args[1]["data"]["From"] == "+15551234567"
        assert call_args[1]["data"]["Body"] == "Hello from Kairos!"

    @patch("httpx.post")
    def test_send_sms_uses_correct_url(self, mock_post: MagicMock, client: TwilioClient) -> None:
        """Should use correct Twilio API URL."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"sid": "SM123"}
        mock_post.return_value = mock_response

        client.send_sms("+447700900123", "Test")

        url = mock_post.call_args[0][0]
        assert url == "https://api.twilio.com/2010-04-01/Accounts/AC1234567890abcdef/Messages.json"

    @patch("httpx.post")
    def test_send_sms_raises_on_error(self, mock_post: MagicMock, client: TwilioClient) -> None:
        """Should raise on API error."""
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=MagicMock()
        )
        mock_post.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            client.send_sms("+447700900123", "Test")


class TestVerifyTwilioSignature:
    """Tests for verify_twilio_signature function."""

    def _compute_signature(self, auth_token: str, url: str, params: dict) -> str:
        """Compute a valid Twilio signature for testing."""
        data = url
        for key in sorted(params.keys()):
            data += key + params[key]

        sig = hmac.new(
            auth_token.encode("utf-8"),
            data.encode("utf-8"),
            hashlib.sha1,
        ).digest()

        return b64encode(sig).decode("utf-8")

    def test_valid_signature(self) -> None:
        """Should return True for valid signature."""
        auth_token = "test_token_12345"
        url = "https://example.com/webhook"
        params = {"Body": "Hello", "From": "+15551234567"}

        signature = self._compute_signature(auth_token, url, params)

        assert verify_twilio_signature(auth_token, signature, url, params) is True

    def test_invalid_signature(self) -> None:
        """Should return False for invalid signature."""
        auth_token = "test_token_12345"
        url = "https://example.com/webhook"
        params = {"Body": "Hello"}

        assert verify_twilio_signature(auth_token, "invalid_sig", url, params) is False

    def test_empty_signature(self) -> None:
        """Should return False for empty signature."""
        assert verify_twilio_signature("token", "", "https://example.com", {}) is False

    def test_wrong_token(self) -> None:
        """Should return False when verified with wrong token."""
        auth_token = "correct_token"
        wrong_token = "wrong_token"
        url = "https://example.com/webhook"
        params = {"Body": "Hello"}

        signature = self._compute_signature(auth_token, url, params)

        assert verify_twilio_signature(wrong_token, signature, url, params) is False

    def test_tampered_params(self) -> None:
        """Should return False when params are tampered."""
        auth_token = "test_token"
        url = "https://example.com/webhook"
        original_params = {"Body": "Hello"}
        tampered_params = {"Body": "Goodbye"}

        signature = self._compute_signature(auth_token, url, original_params)

        assert verify_twilio_signature(auth_token, signature, url, tampered_params) is False

    def test_tampered_url(self) -> None:
        """Should return False when URL is tampered."""
        auth_token = "test_token"
        original_url = "https://example.com/webhook"
        tampered_url = "https://evil.com/webhook"
        params = {"Body": "Hello"}

        signature = self._compute_signature(auth_token, original_url, params)

        assert verify_twilio_signature(auth_token, signature, tampered_url, params) is False

    def test_params_sorted_alphabetically(self) -> None:
        """Should sort params alphabetically for signature."""
        auth_token = "test_token"
        url = "https://example.com/webhook"
        # Params in non-alphabetical order
        params = {"Zebra": "last", "Apple": "first", "Middle": "mid"}

        signature = self._compute_signature(auth_token, url, params)

        # Same params, different order - should still verify
        assert verify_twilio_signature(auth_token, signature, url, params) is True

    def test_empty_params(self) -> None:
        """Should handle empty params dict."""
        auth_token = "test_token"
        url = "https://example.com/webhook"
        params: dict[str, str] = {}

        signature = self._compute_signature(auth_token, url, params)

        assert verify_twilio_signature(auth_token, signature, url, params) is True

    def test_realistic_twilio_params(self) -> None:
        """Should verify signature with realistic Twilio webhook params."""
        auth_token = "my_auth_token_secret"
        url = "https://kairos.example.com/sms-webhook"
        params = {
            "MessageSid": "SM1234567890abcdef",
            "AccountSid": "AC1234567890abcdef",
            "From": "+15551234567",
            "To": "+447700900123",
            "Body": "Yes",
            "NumMedia": "0",
            "NumSegments": "1",
        }

        signature = self._compute_signature(auth_token, url, params)

        assert verify_twilio_signature(auth_token, signature, url, params) is True


class TestBuildTwimlResponse:
    """Tests for build_twiml_response function."""

    def test_empty_response(self) -> None:
        """Should build empty TwiML response."""
        result = build_twiml_response()
        assert result == '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

    def test_none_message(self) -> None:
        """Should build empty response for None message."""
        result = build_twiml_response(None)
        assert result == '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

    def test_with_message(self) -> None:
        """Should build response with message."""
        result = build_twiml_response("Hello!")
        assert "<Message>Hello!</Message>" in result
        assert result.startswith('<?xml version="1.0"')

    def test_escapes_xml_special_chars(self) -> None:
        """Should escape XML special characters."""
        result = build_twiml_response("Test <>&'\" chars")
        assert "&lt;" in result  # <
        assert "&gt;" in result  # >
        assert "&amp;" in result  # &
        assert "&apos;" in result  # '
        assert "&quot;" in result  # "

    def test_multiline_message(self) -> None:
        """Should handle multiline messages."""
        result = build_twiml_response("Line 1\nLine 2")
        assert "Line 1\nLine 2" in result


class TestParseTwilioWebhookBody:
    """Tests for parse_twilio_webhook_body function."""

    def test_parses_simple_body(self) -> None:
        """Should parse simple URL-encoded body."""
        body = "Body=Hello&From=%2B15551234567"
        result = parse_twilio_webhook_body(body)

        assert result["Body"] == "Hello"
        assert result["From"] == "+15551234567"

    def test_parses_full_twilio_webhook(self) -> None:
        """Should parse realistic Twilio webhook body."""
        body = (
            "MessageSid=SM123&AccountSid=AC123&From=%2B15551234567"
            "&To=%2B447700900123&Body=Yes&NumMedia=0&NumSegments=1"
        )
        result = parse_twilio_webhook_body(body)

        assert result["MessageSid"] == "SM123"
        assert result["AccountSid"] == "AC123"
        assert result["From"] == "+15551234567"
        assert result["To"] == "+447700900123"
        assert result["Body"] == "Yes"
        assert result["NumMedia"] == "0"
        assert result["NumSegments"] == "1"

    def test_handles_empty_body(self) -> None:
        """Should handle empty body."""
        result = parse_twilio_webhook_body("")
        assert result == {}

    def test_handles_empty_values(self) -> None:
        """Should handle empty values."""
        body = "Body=&From=%2B15551234567"
        result = parse_twilio_webhook_body(body)

        assert result["Body"] == ""
        assert result["From"] == "+15551234567"

    def test_decodes_url_encoding(self) -> None:
        """Should decode URL-encoded values."""
        body = "Body=Hello%20World%21&From=%2B15551234567"
        result = parse_twilio_webhook_body(body)

        assert result["Body"] == "Hello World!"
        assert result["From"] == "+15551234567"
