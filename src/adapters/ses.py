"""AWS SES adapter for email notifications."""

from __future__ import annotations

import boto3


class SESPublisher:
    """Publisher for AWS SES email messages."""

    def __init__(self, sender_email: str, region: str = "eu-west-1") -> None:
        self.sender_email = sender_email
        self.client = boto3.client("ses", region_name=region)

    def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
    ) -> str:
        """Send an email via SES.

        Args:
            to_email: Recipient email address
            subject: Email subject line
            body: Plain text email body

        Returns:
            The message ID from SES
        """
        response = self.client.send_email(
            Source=self.sender_email,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
        return str(response["MessageId"])
