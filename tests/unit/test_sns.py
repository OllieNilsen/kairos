"""Unit tests for SNS adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.sns import SNSPublisher


class TestSNSPublisher:
    """Tests for SNSPublisher."""

    @pytest.fixture
    def mock_sns_client(self) -> MagicMock:
        """Create a mock SNS client."""
        return MagicMock()

    @pytest.fixture
    def publisher(self, mock_sns_client: MagicMock) -> SNSPublisher:
        """Create publisher with mocked SNS client."""
        with patch("boto3.client") as mock_client:
            mock_client.return_value = mock_sns_client
            pub = SNSPublisher(topic_arn="arn:aws:sns:eu-west-1:123456789:test-topic")
            pub.client = mock_sns_client
            return pub

    def test_init(self) -> None:
        """Should initialize with topic ARN."""
        with patch("boto3.client"):
            publisher = SNSPublisher(topic_arn="arn:aws:sns:eu-west-1:123:topic")
            assert publisher.topic_arn == "arn:aws:sns:eu-west-1:123:topic"

    def test_send_sms(self, publisher: SNSPublisher, mock_sns_client: MagicMock) -> None:
        """Should send SMS via SNS."""
        mock_sns_client.publish.return_value = {"MessageId": "sms-123"}

        message_id = publisher.send_sms(
            message="Hello from Kairos",
            phone_number="+447700900000",
        )

        assert message_id == "sms-123"
        mock_sns_client.publish.assert_called_once_with(
            PhoneNumber="+447700900000",
            Message="Hello from Kairos",
            MessageAttributes={
                "AWS.SNS.SMS.SMSType": {
                    "DataType": "String",
                    "StringValue": "Transactional",
                }
            },
        )

    def test_publish_to_topic(self, publisher: SNSPublisher, mock_sns_client: MagicMock) -> None:
        """Should publish message to topic."""
        mock_sns_client.publish.return_value = {"MessageId": "topic-123"}

        message_id = publisher.publish_to_topic(
            message="Topic message",
        )

        assert message_id == "topic-123"
        mock_sns_client.publish.assert_called_once_with(
            TopicArn="arn:aws:sns:eu-west-1:123456789:test-topic",
            Message="Topic message",
        )

    def test_publish_to_topic_with_subject(
        self, publisher: SNSPublisher, mock_sns_client: MagicMock
    ) -> None:
        """Should include subject when publishing to topic."""
        mock_sns_client.publish.return_value = {"MessageId": "topic-456"}

        message_id = publisher.publish_to_topic(
            message="Topic message",
            subject="Important Update",
        )

        assert message_id == "topic-456"
        call_args = mock_sns_client.publish.call_args
        assert call_args[1]["Subject"] == "Important Update"
