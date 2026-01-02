"""Twilio SMS adapter for sending and receiving SMS messages.

Provides:
- Outbound SMS sending via Twilio REST API
- Webhook signature verification for inbound messages
"""

from __future__ import annotations

import hashlib
import hmac
from base64 import b64encode
from typing import Any

import httpx


class TwilioClient:
    """Client for Twilio SMS API."""

    API_BASE = "https://api.twilio.com/2010-04-01"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
    ) -> None:
        """Initialize Twilio client.

        Args:
            account_sid: Twilio account SID
            auth_token: Twilio auth token (also used for webhook verification)
            from_number: Twilio phone number to send from (E.164 format)
        """
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    def send_sms(self, to: str, body: str) -> str:
        """Send an SMS message.

        Args:
            to: Recipient phone number (E.164 format, e.g., +15551234567)
            body: Message text (max 1600 characters for multi-segment)

        Returns:
            Message SID from Twilio

        Raises:
            httpx.HTTPStatusError: If the API request fails
        """
        url = f"{self.API_BASE}/Accounts/{self.account_sid}/Messages.json"

        response = httpx.post(
            url,
            auth=(self.account_sid, self.auth_token),
            data={
                "To": to,
                "From": self.from_number,
                "Body": body,
            },
        )
        response.raise_for_status()

        data: dict[str, Any] = response.json()
        sid: str = data["sid"]
        return sid


def verify_twilio_signature(
    auth_token: str,
    signature: str,
    url: str,
    params: dict[str, str],
) -> bool:
    """Verify Twilio webhook request signature.

    Twilio signs webhook requests using HMAC-SHA1. This function verifies
    that a request actually came from Twilio.

    See: https://www.twilio.com/docs/usage/security#validating-requests

    Args:
        auth_token: Your Twilio auth token (the signing key)
        signature: The X-Twilio-Signature header value
        url: The full URL of the webhook endpoint (including https://)
        params: The POST parameters from the request body

    Returns:
        True if signature is valid, False otherwise
    """
    if not signature:
        return False

    # Build the data string: URL + sorted params concatenated
    # Twilio sorts params alphabetically by key, then appends key+value
    data = url
    for key in sorted(params.keys()):
        data += key + params[key]

    # Compute HMAC-SHA1
    expected_sig = hmac.new(
        auth_token.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha1,
    ).digest()

    # Base64 encode
    expected_b64 = b64encode(expected_sig).decode("utf-8")

    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_b64, signature)


def build_twiml_response(message: str | None = None) -> str:
    """Build a TwiML response for Twilio webhook.

    Args:
        message: Optional reply message to send back to the user

    Returns:
        TwiML XML string
    """
    if message:
        # Escape XML special characters
        escaped = (
            message.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )
        return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escaped}</Message></Response>'
    else:
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def parse_twilio_webhook_body(body: str) -> dict[str, str]:
    """Parse URL-encoded Twilio webhook body into a dict.

    Twilio sends webhook data as application/x-www-form-urlencoded.

    Args:
        body: URL-encoded request body

    Returns:
        Dict of parameter name -> value
    """
    from urllib.parse import parse_qs

    parsed = parse_qs(body, keep_blank_values=True)
    # parse_qs returns lists; we want single values
    return {k: v[0] if v else "" for k, v in parsed.items()}
