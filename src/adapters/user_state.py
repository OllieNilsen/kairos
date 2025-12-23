"""DynamoDB repository for user state management."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

# Support both Lambda (core.models) and test (src.core.models) import paths
try:
    from core.models import UserState
except ImportError:
    from src.core.models import UserState


class UserStateRepository:
    """Repository for user state in DynamoDB."""

    def __init__(self, table_name: str, region: str = "eu-west-1") -> None:
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    def get_user_state(self, user_id: str) -> UserState | None:
        """Get user state from DynamoDB.

        Args:
            user_id: The user identifier

        Returns:
            UserState if found, None otherwise
        """
        response = self.table.get_item(Key={"user_id": user_id})
        item = response.get("Item")

        if not item:
            return None

        return self._item_to_state(item)

    def save_user_state(self, state: UserState) -> None:
        """Save user state to DynamoDB (full replace).

        Args:
            state: The UserState to save
        """
        item = self._state_to_item(state)
        self.table.put_item(Item=item)

    def reset_daily_state(
        self,
        user_id: str,
        next_prompt_at: str,
        prompt_schedule_name: str | None = None,
        debrief_event_id: str | None = None,
        debrief_event_etag: str | None = None,
    ) -> None:
        """Reset daily state for a new day.

        Called by daily_plan_prompt at 8am to prepare for the day.

        Args:
            user_id: The user identifier
            next_prompt_at: ISO8601 timestamp for when to send prompt
            prompt_schedule_name: Name of the EventBridge schedule
            debrief_event_id: Google Calendar event ID for the debrief
            debrief_event_etag: Etag of the calendar event
        """
        now = datetime.now(UTC).isoformat()

        update_expr = """
            SET prompts_sent_today = :zero,
                awaiting_reply = :false,
                active_prompt_id = :null,
                daily_call_made = :false,
                call_successful = :false,
                retries_today = :zero,
                next_retry_at = :null,
                retry_schedule_name = :null,
                last_daily_reset = :now,
                next_prompt_at = :next_prompt
        """
        expr_values: dict[str, Any] = {
            ":zero": 0,
            ":false": False,
            ":null": None,
            ":now": now,
            ":next_prompt": next_prompt_at,
        }

        if prompt_schedule_name is not None:
            update_expr += ", prompt_schedule_name = :schedule"
            expr_values[":schedule"] = prompt_schedule_name

        if debrief_event_id is not None:
            update_expr += ", debrief_event_id = :event_id"
            expr_values[":event_id"] = debrief_event_id

        if debrief_event_etag is not None:
            update_expr += ", debrief_event_etag = :event_etag"
            expr_values[":event_etag"] = debrief_event_etag

        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

    def record_prompt_sent(self, user_id: str, prompt_id: str) -> bool:
        """Record that a prompt SMS was sent.

        Uses conditional write to ensure only one prompt per day.

        Args:
            user_id: The user identifier
            prompt_id: Unique identifier for this prompt (e.g., user_id#YYYY-MM-DD)

        Returns:
            True if this is the first prompt, False if already sent today
        """
        now = datetime.now(UTC).isoformat()

        try:
            self.table.update_item(
                Key={"user_id": user_id},
                UpdateExpression="""
                    SET prompts_sent_today = prompts_sent_today + :one,
                        last_prompt_at = :now,
                        awaiting_reply = :true,
                        active_prompt_id = :pid
                """,
                ConditionExpression="prompts_sent_today < :max",
                ExpressionAttributeValues={
                    ":one": 1,
                    ":now": now,
                    ":true": True,
                    ":pid": prompt_id,
                    ":max": 1,  # Max 1 prompt per day
                },
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def record_call_initiated(self, user_id: str, batch_id: str) -> bool:
        """Record that a daily call was initiated.

        Uses conditional write to ensure only one call per day.

        Args:
            user_id: The user identifier
            batch_id: Unique batch identifier (e.g., user_id#YYYY-MM-DD)

        Returns:
            True if this is the first call, False if already called today
        """
        now = datetime.now(UTC).isoformat()

        try:
            self.table.update_item(
                Key={"user_id": user_id},
                UpdateExpression="""
                    SET daily_call_made = :true,
                        last_call_at = :now,
                        awaiting_reply = :false,
                        daily_batch_id = :bid
                """,
                ConditionExpression="daily_call_made = :false",
                ExpressionAttributeValues={
                    ":true": True,
                    ":false": False,
                    ":now": now,
                    ":bid": batch_id,
                },
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def set_snooze(self, user_id: str, snooze_until: str) -> None:
        """Set snooze until timestamp (user said NO).

        Args:
            user_id: The user identifier
            snooze_until: ISO8601 timestamp to snooze until
        """
        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET snooze_until = :until, awaiting_reply = :false",
            ExpressionAttributeValues={
                ":until": snooze_until,
                ":false": False,
            },
        )

    def clear_snooze(self, user_id: str) -> None:
        """Clear snooze (user said READY).

        Args:
            user_id: The user identifier
        """
        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET snooze_until = :null",
            ExpressionAttributeValues={":null": None},
        )

    def set_stop(self, user_id: str, stop: bool = True) -> None:
        """Set or clear the stop flag (user said STOP).

        Args:
            user_id: The user identifier
            stop: Whether to stop all prompts/calls
        """
        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET stopped = :stop",
            ExpressionAttributeValues={":stop": stop},
        )

    def update_prompt_schedule(
        self,
        user_id: str,
        next_prompt_at: str,
        prompt_schedule_name: str | None = None,
    ) -> None:
        """Update the prompt time and schedule name (when user moves calendar event).

        Args:
            user_id: The user identifier
            next_prompt_at: New ISO8601 timestamp for prompt
            prompt_schedule_name: New EventBridge schedule name (if changed)
        """
        update_expr = "SET next_prompt_at = :next_prompt"
        expr_values: dict[str, Any] = {":next_prompt": next_prompt_at}

        if prompt_schedule_name is not None:
            update_expr += ", prompt_schedule_name = :schedule"
            expr_values[":schedule"] = prompt_schedule_name

        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

    def clear_debrief_event(self, user_id: str) -> None:
        """Clear debrief event info (user deleted the calendar event).

        Args:
            user_id: The user identifier
        """
        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="""
                SET debrief_event_id = :null,
                    debrief_event_etag = :null,
                    next_prompt_at = :null,
                    prompt_schedule_name = :null
            """,
            ExpressionAttributeValues={":null": None},
        )

    def update_debrief_event(
        self,
        user_id: str,
        debrief_event_id: str,
        debrief_event_etag: str | None = None,
    ) -> None:
        """Update debrief event info (etag changed, or event modified).

        Args:
            user_id: The user identifier
            debrief_event_id: Google Calendar event ID
            debrief_event_etag: New etag from Google
        """
        update_expr = "SET debrief_event_id = :event_id"
        expr_values: dict[str, Any] = {":event_id": debrief_event_id}

        if debrief_event_etag is not None:
            update_expr += ", debrief_event_etag = :etag"
            expr_values[":etag"] = debrief_event_etag

        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

    def can_prompt(self, state: UserState | None) -> tuple[bool, str]:
        """Check if we can send a prompt to the user.

        Args:
            state: The user's current state (or None if not found)

        Returns:
            Tuple of (can_prompt, reason)
        """
        if state is None:
            return False, "user_not_found"

        if state.stopped:
            return False, "stopped"

        if state.prompts_sent_today >= 1:
            return False, "prompt_already_sent"

        if state.snooze_until:
            try:
                snooze_dt = datetime.fromisoformat(state.snooze_until)
                if datetime.now(UTC) < snooze_dt:
                    return False, "snoozed"
            except ValueError:
                pass

        return True, "ok"

    def can_call(self, state: UserState | None) -> tuple[bool, str]:
        """Check if we can initiate a call for the user.

        Args:
            state: The user's current state (or None if not found)

        Returns:
            Tuple of (can_call, reason)
        """
        if state is None:
            return False, "user_not_found"

        if state.stopped:
            return False, "stopped"

        # Only block if call was already successful (allow retries)
        if state.daily_call_made and state.call_successful:
            return False, "call_already_successful"

        if state.snooze_until:
            try:
                snooze_dt = datetime.fromisoformat(state.snooze_until)
                if datetime.now(UTC) < snooze_dt:
                    return False, "snoozed"
            except ValueError:
                pass

        return True, "ok"

    def can_retry(self, state: UserState | None, max_retries: int = 3) -> tuple[bool, str]:
        """Check if we can retry a call for the user.

        Args:
            state: The user's current state (or None if not found)
            max_retries: Maximum number of retries allowed per day

        Returns:
            Tuple of (can_retry, reason)
        """
        if state is None:
            return False, "user_not_found"

        if state.stopped:
            return False, "stopped"

        if state.call_successful:
            return False, "call_already_successful"

        if state.retries_today >= max_retries:
            return False, "max_retries_reached"

        if state.snooze_until:
            try:
                snooze_dt = datetime.fromisoformat(state.snooze_until)
                if datetime.now(UTC) < snooze_dt:
                    return False, "snoozed"
            except ValueError:
                pass

        return True, "ok"

    def record_call_success(self, user_id: str) -> None:
        """Mark the daily call as successful.

        Args:
            user_id: The user identifier
        """
        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET call_successful = :true",
            ExpressionAttributeValues={":true": True},
        )

    def record_retry_scheduled(
        self,
        user_id: str,
        next_retry_at: str,
        retry_schedule_name: str,
    ) -> None:
        """Record that a retry has been scheduled.

        Args:
            user_id: The user identifier
            next_retry_at: ISO8601 timestamp for the retry
            retry_schedule_name: EventBridge Scheduler schedule name
        """
        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="""
                SET retries_today = retries_today + :one,
                    next_retry_at = :next_retry,
                    retry_schedule_name = :schedule_name,
                    daily_call_made = :false
            """,
            ExpressionAttributeValues={
                ":one": 1,
                ":next_retry": next_retry_at,
                ":schedule_name": retry_schedule_name,
                ":false": False,  # Reset so prompt_sender can run again
            },
        )

    def clear_retry_schedule(self, user_id: str) -> None:
        """Clear retry schedule info after successful call or max retries.

        Args:
            user_id: The user identifier
        """
        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET next_retry_at = :null, retry_schedule_name = :null",
            ExpressionAttributeValues={":null": None},
        )

    def _item_to_state(self, item: dict[str, Any]) -> UserState:
        """Convert DynamoDB item to UserState model."""
        return UserState(
            user_id=item["user_id"],
            phone_number=item.get("phone_number"),
            email=item.get("email"),
            timezone=item.get("timezone", "Europe/London"),
            preferred_prompt_time=item.get("preferred_prompt_time", "17:30"),
            next_prompt_at=item.get("next_prompt_at"),
            prompt_schedule_name=item.get("prompt_schedule_name"),
            debrief_event_id=item.get("debrief_event_id"),
            debrief_event_etag=item.get("debrief_event_etag"),
            # Daily state
            prompts_sent_today=item.get("prompts_sent_today", 0),
            last_prompt_at=item.get("last_prompt_at"),
            awaiting_reply=item.get("awaiting_reply", False),
            active_prompt_id=item.get("active_prompt_id"),
            daily_call_made=item.get("daily_call_made", False),
            call_successful=item.get("call_successful", False),
            retries_today=item.get("retries_today", 0),
            last_call_at=item.get("last_call_at"),
            next_retry_at=item.get("next_retry_at"),
            retry_schedule_name=item.get("retry_schedule_name"),
            daily_batch_id=item.get("daily_batch_id"),
            last_daily_reset=item.get("last_daily_reset"),
            # Control state
            snooze_until=item.get("snooze_until"),
            stopped=item.get("stopped", False),
            # Google Calendar push subscription (refresh token is in SSM)
            google_channel_id=item.get("google_channel_id"),
            google_channel_expiry=item.get("google_channel_expiry"),
        )

    def _state_to_item(self, state: UserState) -> dict[str, Any]:
        """Convert UserState model to DynamoDB item."""
        item: dict[str, Any] = {
            "user_id": state.user_id,
            "timezone": state.timezone,
            "preferred_prompt_time": state.preferred_prompt_time,
            "prompts_sent_today": state.prompts_sent_today,
            "awaiting_reply": state.awaiting_reply,
            "daily_call_made": state.daily_call_made,
            "call_successful": state.call_successful,
            "retries_today": state.retries_today,
            "stopped": state.stopped,
        }

        # Add optional fields if they have values
        optional_fields = [
            "phone_number",
            "email",
            "next_prompt_at",
            "prompt_schedule_name",
            "debrief_event_id",
            "debrief_event_etag",
            "last_prompt_at",
            "active_prompt_id",
            "last_call_at",
            "next_retry_at",
            "retry_schedule_name",
            "daily_batch_id",
            "last_daily_reset",
            "snooze_until",
            "google_channel_id",
            "google_channel_expiry",
        ]

        for field in optional_fields:
            value = getattr(state, field)
            if value is not None:
                item[field] = value

        return item
