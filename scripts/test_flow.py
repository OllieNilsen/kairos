#!/usr/bin/env python3
"""Test script for the Kairos daily debrief flow.

This script helps test the end-to-end flow by:
1. Creating a test user state in kairos-user-state
2. Creating test meetings in kairos-meetings
3. Invoking the daily planner Lambda
4. Optionally invoking the prompt sender Lambda

Usage:
    python scripts/test_flow.py setup          # Create test user + meetings
    python scripts/test_flow.py plan           # Invoke daily planner
    python scripts/test_flow.py call           # Invoke prompt sender (triggers call)
    python scripts/test_flow.py status         # Show current state
    python scripts/test_flow.py cleanup        # Remove test data
"""

import argparse
import json
from datetime import UTC, datetime, timedelta

import boto3

# Configuration
REGION = "eu-west-1"
USER_STATE_TABLE = "kairos-user-state"
MEETINGS_TABLE = "kairos-meetings"
IDEMPOTENCY_TABLE = "kairos-idempotency"
DAILY_PLAN_LAMBDA = "kairos-daily-plan"
PROMPT_SENDER_LAMBDA = "kairos-prompt-sender"

# Test user configuration
TEST_USER_ID = "user-001"


def get_dynamodb():
    return boto3.resource("dynamodb", region_name=REGION)


def get_lambda():
    return boto3.client("lambda", region_name=REGION)


def setup_test_user(phone_number: str | None = None):
    """Create or update the test user state."""
    dynamodb = get_dynamodb()
    table = dynamodb.Table(USER_STATE_TABLE)

    # Get phone from SSM if not provided
    if not phone_number:
        try:
            ssm = boto3.client("ssm", region_name=REGION)
            resp = ssm.get_parameter(Name="/kairos/user-phone-number")
            phone_number = resp["Parameter"]["Value"]
        except Exception:
            phone_number = "+447000000000"  # Placeholder
            print(f"⚠️  Using placeholder phone: {phone_number}")
            print(
                "   Set real phone with: aws ssm put-parameter --name /kairos/user-phone-number --value +44... --type String"
            )

    user_state = {
        "user_id": TEST_USER_ID,
        "phone_number": phone_number,
        "timezone": "Europe/London",
        "preferred_prompt_time": "17:30",
        "prompts_sent_today": 0,
        "daily_call_made": False,
        "stopped": False,
        "awaiting_reply": False,
    }

    table.put_item(Item=user_state)
    print(f"✅ Created/updated user state for {TEST_USER_ID}")
    print(f"   Phone: {phone_number}")
    print("   Preferred time: 17:30 Europe/London")
    return user_state


def setup_test_meetings():
    """Create test meetings that ended today."""
    dynamodb = get_dynamodb()
    table = dynamodb.Table(MEETINGS_TABLE)

    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")

    # Create 2-3 test meetings that "ended" earlier today
    meetings = [
        {
            "user_id": TEST_USER_ID,
            "meeting_id": f"test-meeting-1-{today}",
            "title": "Q4 Planning Review",
            "description": "Review Q4 goals and progress",
            "start_time": (now - timedelta(hours=3)).isoformat(),
            "end_time": (now - timedelta(hours=2)).isoformat(),
            "attendees": ["Alice Smith", "Bob Jones"],
            "status": "pending",
            "created_at": now.isoformat(),
            "ttl": int(now.timestamp()) + 86400 * 7,  # 7 days
        },
        {
            "user_id": TEST_USER_ID,
            "meeting_id": f"test-meeting-2-{today}",
            "title": "Product Roadmap Discussion",
            "description": "Discuss H1 2025 roadmap priorities",
            "start_time": (now - timedelta(hours=2)).isoformat(),
            "end_time": (now - timedelta(hours=1)).isoformat(),
            "attendees": ["Carol White", "David Lee"],
            "status": "pending",
            "created_at": now.isoformat(),
            "ttl": int(now.timestamp()) + 86400 * 7,
        },
        {
            "user_id": TEST_USER_ID,
            "meeting_id": f"test-meeting-3-{today}",
            "title": "1:1 with Manager",
            "start_time": (now - timedelta(hours=1)).isoformat(),
            "end_time": (now - timedelta(minutes=30)).isoformat(),
            "attendees": ["Your Manager"],
            "status": "pending",
            "created_at": now.isoformat(),
            "ttl": int(now.timestamp()) + 86400 * 7,
        },
    ]

    for meeting in meetings:
        table.put_item(Item=meeting)
        print(f"✅ Created meeting: {meeting['title']}")

    print(f"\n📅 Created {len(meetings)} test meetings for today")
    return meetings


def clear_idempotency_keys():
    """Clear today's idempotency keys to allow re-testing."""
    dynamodb = get_dynamodb()
    table = dynamodb.Table(IDEMPOTENCY_TABLE)

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    keys_to_delete = [
        f"daily-plan:{TEST_USER_ID}#{today}",
        f"call-batch:{TEST_USER_ID}#{today}",
        f"sms-send:{TEST_USER_ID}#{today}",
    ]

    for key in keys_to_delete:
        try:
            table.delete_item(Key={"idempotency_key": key})
            print(f"🗑️  Cleared idempotency key: {key}")
        except Exception:
            pass


def invoke_daily_planner():
    """Invoke the daily planning Lambda."""
    lambda_client = get_lambda()

    print(f"\n🚀 Invoking {DAILY_PLAN_LAMBDA}...")

    response = lambda_client.invoke(
        FunctionName=DAILY_PLAN_LAMBDA,
        InvocationType="RequestResponse",
        Payload=json.dumps({"source": "test_script"}),
    )

    payload = json.loads(response["Payload"].read())
    print("\n📋 Response:")
    print(json.dumps(payload, indent=2))

    if response.get("FunctionError"):
        print(f"\n❌ Lambda error: {response['FunctionError']}")
    else:
        print("\n✅ Daily planner completed")

    return payload


def invoke_prompt_sender():
    """Invoke the prompt sender Lambda (will trigger a call!)."""
    lambda_client = get_lambda()

    today = datetime.now(UTC).strftime("%Y-%m-%d")

    print(f"\n🚀 Invoking {PROMPT_SENDER_LAMBDA}...")
    print("⚠️  This will initiate a REAL phone call if you have pending meetings!")

    confirm = input("Continue? [y/N]: ")
    if confirm.lower() != "y":
        print("Cancelled.")
        return None

    response = lambda_client.invoke(
        FunctionName=PROMPT_SENDER_LAMBDA,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {
                "user_id": TEST_USER_ID,
                "date": today,
                "scheduled_time": datetime.now(UTC).isoformat(),
            }
        ),
    )

    payload = json.loads(response["Payload"].read())
    print("\n📋 Response:")
    print(json.dumps(payload, indent=2))

    if response.get("FunctionError"):
        print(f"\n❌ Lambda error: {response['FunctionError']}")
    else:
        print("\n✅ Prompt sender completed")

    return payload


def show_status():
    """Show current state of user and meetings."""
    dynamodb = get_dynamodb()

    # User state
    user_table = dynamodb.Table(USER_STATE_TABLE)
    user_resp = user_table.get_item(Key={"user_id": TEST_USER_ID})
    user = user_resp.get("Item")

    print("\n👤 User State:")
    if user:
        print(json.dumps(user, indent=2, default=str))
    else:
        print("   (not found)")

    # Meetings
    meetings_table = dynamodb.Table(MEETINGS_TABLE)
    meetings_resp = meetings_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("user_id").eq(TEST_USER_ID)
    )
    meetings = meetings_resp.get("Items", [])

    print(f"\n📅 Meetings ({len(meetings)} total):")
    for m in meetings:
        status_icon = "✅" if m.get("status") == "debriefed" else "⏳"
        print(f"   {status_icon} {m['title']} - {m.get('status', 'pending')}")

    # Idempotency keys
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    idemp_table = dynamodb.Table(IDEMPOTENCY_TABLE)

    print("\n🔒 Idempotency Keys (today):")
    for prefix in ["daily-plan", "call-batch", "sms-send"]:
        key = f"{prefix}:{TEST_USER_ID}#{today}"
        resp = idemp_table.get_item(Key={"idempotency_key": key})
        if resp.get("Item"):
            print(f"   ✓ {key}")
        else:
            print(f"   ○ {key} (not set)")


def cleanup():
    """Remove test data."""
    dynamodb = get_dynamodb()

    # Delete user state
    user_table = dynamodb.Table(USER_STATE_TABLE)
    user_table.delete_item(Key={"user_id": TEST_USER_ID})
    print(f"🗑️  Deleted user state for {TEST_USER_ID}")

    # Delete meetings
    meetings_table = dynamodb.Table(MEETINGS_TABLE)
    meetings_resp = meetings_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("user_id").eq(TEST_USER_ID)
    )
    for item in meetings_resp.get("Items", []):
        meetings_table.delete_item(
            Key={"user_id": item["user_id"], "meeting_id": item["meeting_id"]}
        )
    print(f"🗑️  Deleted {len(meetings_resp.get('Items', []))} meetings")

    # Clear idempotency keys
    clear_idempotency_keys()

    print("\n✅ Cleanup complete")


def main():
    parser = argparse.ArgumentParser(description="Test Kairos daily debrief flow")
    parser.add_argument(
        "command",
        choices=["setup", "plan", "call", "status", "cleanup", "reset"],
        help="Command to run",
    )
    parser.add_argument(
        "--phone",
        help="Phone number for test user (E.164 format)",
    )

    args = parser.parse_args()

    if args.command == "setup":
        setup_test_user(args.phone)
        setup_test_meetings()
        print("\n📝 Next steps:")
        print("   1. Run: python scripts/test_flow.py status")
        print("   2. Run: python scripts/test_flow.py plan")
        print("   3. Run: python scripts/test_flow.py call  (triggers real call!)")

    elif args.command == "plan":
        invoke_daily_planner()

    elif args.command == "call":
        invoke_prompt_sender()

    elif args.command == "status":
        show_status()

    elif args.command == "cleanup":
        cleanup()

    elif args.command == "reset":
        # Clear idempotency keys to allow re-testing
        clear_idempotency_keys()
        print("\n✅ Reset complete - you can re-run plan/call")


if __name__ == "__main__":
    main()
