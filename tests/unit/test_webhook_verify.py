"""Unit tests for webhook signature verification."""

import hashlib
import hmac

import pytest

from src.adapters.webhook_verify import verify_bland_signature


class TestVerifyBlandSignature:
    """Tests for Bland webhook signature verification."""

    def test_valid_signature(self):
        """Valid signature should return True."""
        secret = "test-secret-key"
        body = '{"call_id":"abc123","status":"completed"}'

        # Calculate expected signature
        signature = hmac.new(
            key=secret.encode(),
            msg=body.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

        assert verify_bland_signature(secret, body, signature) is True

    def test_invalid_signature(self):
        """Invalid signature should return False."""
        secret = "test-secret-key"
        body = '{"call_id":"abc123","status":"completed"}'

        assert verify_bland_signature(secret, body, "invalid-signature") is False

    def test_wrong_secret(self):
        """Wrong secret should produce different signature."""
        correct_secret = "correct-secret"
        wrong_secret = "wrong-secret"
        body = '{"call_id":"abc123"}'

        signature = hmac.new(
            key=correct_secret.encode(),
            msg=body.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

        assert verify_bland_signature(wrong_secret, body, signature) is False

    def test_tampered_body(self):
        """Tampered body should fail verification."""
        secret = "test-secret"
        original_body = '{"call_id":"abc123","status":"completed"}'
        tampered_body = '{"call_id":"abc123","status":"failed"}'

        signature = hmac.new(
            key=secret.encode(),
            msg=original_body.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

        assert verify_bland_signature(secret, tampered_body, signature) is False

    def test_empty_body(self):
        """Empty body should still work."""
        secret = "test-secret"
        body = ""

        signature = hmac.new(
            key=secret.encode(),
            msg=body.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

        assert verify_bland_signature(secret, body, signature) is True

    @pytest.mark.parametrize(
        "signature",
        [
            "",
            "abc",
            "0" * 64,  # Valid hex length but wrong value
        ],
    )
    def test_various_invalid_signatures(self, signature):
        """Various invalid signatures should all return False."""
        secret = "test-secret"
        body = '{"data":"test"}'

        assert verify_bland_signature(secret, body, signature) is False
