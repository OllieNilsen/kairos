"""Bland AI webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac


def verify_bland_signature(secret: str, body: str, signature: str) -> bool:
    """Verify Bland AI webhook signature using HMAC-SHA256.

    Args:
        secret: The webhook signing secret from Bland dashboard.
        body: The raw request body string (JSON).
        signature: The value from X-Webhook-Signature header.

    Returns:
        True if signature is valid, False otherwise.
    """
    expected = hmac.new(
        key=secret.encode(),
        msg=body.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected, signature)
