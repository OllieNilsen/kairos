#!/usr/bin/env python3
"""Set up Google Calendar push notifications (watch).

Run this to start receiving calendar change notifications:
    python scripts/setup_calendar_watch.py

The watch expires after 7 days and needs to be renewed.
"""

import json
import sys
import uuid
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import boto3

# Get the calendar webhook URL from CloudFormation
def get_calendar_webhook_url() -> str:
    """Get the calendar webhook URL from CloudFormation outputs."""
    cf = boto3.client("cloudformation", region_name="eu-west-1")
    response = cf.describe_stacks(StackName="KairosStack")
    outputs = response["Stacks"][0]["Outputs"]

    for output in outputs:
        if output["OutputKey"] == "CalendarWebhookUrl":
            return output["OutputValue"]

    raise ValueError("CalendarWebhookUrl not found in stack outputs")


def get_ssm_parameter(name: str, decrypt: bool = True) -> str:
    """Get a parameter from SSM."""
    ssm = boto3.client("ssm", region_name="eu-west-1")
    response = ssm.get_parameter(Name=name, WithDecryption=decrypt)
    return response["Parameter"]["Value"]


def main():
    # Import here after path is set
    from adapters.google_calendar import GoogleCalendarClient

    print("Setting up Google Calendar watch...")

    # Get credentials from SSM
    client_id = get_ssm_parameter("/kairos/google-client-id", decrypt=False)
    client_secret = get_ssm_parameter("/kairos/google-client-secret")
    refresh_token = get_ssm_parameter("/kairos/google-refresh-token")

    # Create calendar client
    calendar = GoogleCalendarClient(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )

    # Get webhook URL
    webhook_url = get_calendar_webhook_url()
    print(f"Webhook URL: {webhook_url}")

    # Generate a unique channel ID
    channel_id = f"kairos-calendar-{uuid.uuid4().hex[:8]}"
    print(f"Channel ID: {channel_id}")

    # Set up the watch
    try:
        result = calendar.watch_calendar(
            webhook_url=webhook_url,
            channel_id=channel_id,
            calendar_id="primary",
            ttl_seconds=604800,  # 7 days (maximum)
        )

        print("\n" + "=" * 60)
        print("SUCCESS! Calendar watch created:")
        print("=" * 60)
        print(json.dumps(result, indent=2))
        print("=" * 60)

        # Store the channel info for later (e.g., to stop the watch)
        print("\nStore these values to stop the watch later:")
        print(f"  Channel ID: {channel_id}")
        print(f"  Resource ID: {result.get('resourceId')}")
        print(f"  Expiration: {result.get('expiration')}")

        # Calculate expiration time
        import datetime

        expiration_ms = int(result.get("expiration", 0))
        if expiration_ms:
            expiration_dt = datetime.datetime.fromtimestamp(expiration_ms / 1000)
            print(f"  Expires at: {expiration_dt.isoformat()}")

    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nMake sure:")
        print("1. Your webhook URL is publicly accessible (HTTPS)")
        print("2. Google can reach your Lambda function")
        print("3. Your OAuth credentials are valid")
        raise


if __name__ == "__main__":
    main()

