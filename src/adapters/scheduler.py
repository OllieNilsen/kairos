"""EventBridge Scheduler adapter for one-time and recurring schedules."""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class SchedulerClient:
    """Client for AWS EventBridge Scheduler.

    Used for:
    - Creating one-time schedules (prompt sender at specific time)
    - Updating schedules (when user moves debrief event)
    - Deleting schedules (when user deletes debrief event or day completes)
    """

    def __init__(
        self,
        region: str = "eu-west-1",
        schedule_group: str = "default",
    ) -> None:
        """Initialize the scheduler client.

        Args:
            region: AWS region
            schedule_group: Schedule group name (default group is "default")
        """
        self.region = region
        self.schedule_group = schedule_group
        self.client = boto3.client("scheduler", region_name=region)

    def upsert_one_time_schedule(
        self,
        name: str,
        at_time_utc_iso: str,
        target_arn: str,
        payload: dict[str, Any],
        role_arn: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create or update a one-time schedule.

        If the schedule exists, it will be updated. Otherwise, it will be created.
        Uses "at()" schedule expression for exact time execution.

        Args:
            name: Unique schedule name (e.g., "kairos-prompt-user123-2024-01-15")
            at_time_utc_iso: ISO8601 UTC timestamp (e.g., "2024-01-15T17:30:00Z")
            target_arn: Target Lambda ARN
            payload: JSON payload to pass to the Lambda
            role_arn: IAM role ARN for scheduler to assume
            description: Optional description

        Returns:
            Schedule response from AWS
        """
        # EventBridge Scheduler uses at() expression for one-time schedules
        # Format: at(yyyy-mm-ddThh:mm:ss)
        # Remove the 'Z' and any timezone info for the at() expression
        schedule_time = at_time_utc_iso.replace("Z", "").replace("+00:00", "")
        schedule_expression = f"at({schedule_time})"

        schedule_params = {
            "Name": name,
            "GroupName": self.schedule_group,
            "ScheduleExpression": schedule_expression,
            "ScheduleExpressionTimezone": "UTC",
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Target": {
                "Arn": target_arn,
                "RoleArn": role_arn,
                "Input": json.dumps(payload),
            },
            "Description": description,
            # Delete after invocation to avoid orphan schedules
            "ActionAfterCompletion": "DELETE",
        }

        try:
            # Try to update existing schedule
            response: dict[str, Any] = self.client.update_schedule(**schedule_params)
            logger.info("Updated schedule %s for %s", name, at_time_utc_iso)
            return response
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                # Schedule doesn't exist, create it
                response = self.client.create_schedule(**schedule_params)
                logger.info("Created schedule %s for %s", name, at_time_utc_iso)
                return response
            raise

    def delete_schedule(self, name: str) -> bool:
        """Delete a schedule (best-effort).

        Args:
            name: Schedule name to delete

        Returns:
            True if deleted or didn't exist, False on error
        """
        try:
            self.client.delete_schedule(
                Name=name,
                GroupName=self.schedule_group,
            )
            logger.info("Deleted schedule %s", name)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.info("Schedule %s not found (already deleted)", name)
                return True
            logger.error("Failed to delete schedule %s: %s", name, e)
            return False

    def get_schedule(self, name: str) -> dict[str, Any] | None:
        """Get schedule details.

        Args:
            name: Schedule name

        Returns:
            Schedule details or None if not found
        """
        try:
            response: dict[str, Any] = self.client.get_schedule(
                Name=name,
                GroupName=self.schedule_group,
            )
            return response
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise


def make_prompt_schedule_name(user_id: str, date_str: str) -> str:
    """Generate the deterministic schedule name for a daily prompt.

    Args:
        user_id: User identifier
        date_str: Date string (YYYY-MM-DD)

    Returns:
        Schedule name string
    """
    # Sanitize user_id to be schedule-name-safe (alphanumeric, hyphens, underscores)
    safe_user_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in user_id)
    return f"kairos-prompt-{safe_user_id}-{date_str}"


def make_retry_schedule_name(user_id: str, date_str: str, retry_number: int) -> str:
    """Generate the deterministic schedule name for a call retry.

    Args:
        user_id: User identifier
        date_str: Date string (YYYY-MM-DD)
        retry_number: Retry attempt number (1, 2, 3)

    Returns:
        Schedule name string
    """
    safe_user_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in user_id)
    return f"kairos-retry-{safe_user_id}-{date_str}-{retry_number}"
