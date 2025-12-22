"""AWS SNS adapter for SMS notifications."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_sns import SNSClient


class SNSPublisher:
    """Publisher for AWS SNS SMS messages."""

    def __init__(self, topic_arn: str, region: str = "eu-west-1") -> None:
        self.topic_arn = topic_arn
        self.client: SNSClient = boto3.client("sns", region_name=region)

    def send_sms(self, message: str, phone_number: str) -> str:
        """Send an SMS message via SNS.

        Args:
            message: The message content (max 160 chars recommended)
            phone_number: The E.164 formatted phone number

        Returns:
            The message ID from SNS
        """
        # Direct SMS publish (bypasses topic for single recipient)
        response = self.client.publish(
            PhoneNumber=phone_number,
            Message=message,
            MessageAttributes={
                "AWS.SNS.SMS.SMSType": {
                    "DataType": "String",
                    "StringValue": "Transactional",
                }
            },
        )
        return response["MessageId"]

    def publish_to_topic(self, message: str, subject: str | None = None) -> str:
        """Publish a message to the SNS topic.

        Args:
            message: The message content
            subject: Optional subject line

        Returns:
            The message ID from SNS
        """
        kwargs: dict[str, Any] = {
            "TopicArn": self.topic_arn,
            "Message": message,
        }
        if subject:
            kwargs["Subject"] = subject

        response = self.client.publish(**kwargs)
        return response["MessageId"]
