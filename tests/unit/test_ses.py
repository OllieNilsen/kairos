"""Unit tests for SES adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.ses import SESPublisher


class TestSESPublisher:
    """Tests for SESPublisher."""

    @pytest.fixture
    def mock_ses_client(self) -> MagicMock:
        """Create a mock SES client."""
        return MagicMock()

    @pytest.fixture
    def publisher(self, mock_ses_client: MagicMock) -> SESPublisher:
        """Create publisher with mocked SES client."""
        with patch("boto3.client") as mock_client:
            mock_client.return_value = mock_ses_client
            pub = SESPublisher(sender_email="sender@example.com")
            pub.client = mock_ses_client
            return pub

    def test_init(self) -> None:
        """Should initialize with sender email."""
        with patch("boto3.client"):
            publisher = SESPublisher(sender_email="test@example.com")
            assert publisher.sender_email == "test@example.com"

    def test_init_with_custom_region(self) -> None:
        """Should use custom region."""
        with patch("boto3.client") as mock_client:
            SESPublisher(sender_email="test@example.com", region="us-east-1")
            mock_client.assert_called_with("ses", region_name="us-east-1")

    def test_send_email(self, publisher: SESPublisher, mock_ses_client: MagicMock) -> None:
        """Should send email via SES."""
        mock_ses_client.send_email.return_value = {"MessageId": "msg-123"}

        message_id = publisher.send_email(
            to_email="recipient@example.com",
            subject="Test Subject",
            body="Test body content",
        )

        assert message_id == "msg-123"
        mock_ses_client.send_email.assert_called_once_with(
            Source="sender@example.com",
            Destination={"ToAddresses": ["recipient@example.com"]},
            Message={
                "Subject": {"Data": "Test Subject", "Charset": "UTF-8"},
                "Body": {"Text": {"Data": "Test body content", "Charset": "UTF-8"}},
            },
        )

    def test_send_email_with_unicode(
        self, publisher: SESPublisher, mock_ses_client: MagicMock
    ) -> None:
        """Should handle unicode in email content."""
        mock_ses_client.send_email.return_value = {"MessageId": "msg-456"}

        message_id = publisher.send_email(
            to_email="recipient@example.com",
            subject="Résumé: Møting Summary 📧",
            body="Meeting notes with émojis 🎉 and ümlauts",
        )

        assert message_id == "msg-456"
        call_args = mock_ses_client.send_email.call_args
        assert call_args[1]["Message"]["Subject"]["Data"] == "Résumé: Møting Summary 📧"
