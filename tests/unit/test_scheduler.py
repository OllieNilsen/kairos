"""Unit tests for EventBridge Scheduler adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.adapters.scheduler import SchedulerClient, make_prompt_schedule_name


class TestMakePromptScheduleName:
    """Tests for make_prompt_schedule_name helper."""

    def test_generates_correct_format(self) -> None:
        """Should generate kairos-prompt-{user_id}-{date} format."""
        result = make_prompt_schedule_name("user-001", "2024-01-15")
        assert result == "kairos-prompt-user-001-2024-01-15"

    def test_sanitizes_special_characters(self) -> None:
        """Should replace special characters with hyphens."""
        result = make_prompt_schedule_name("user@email.com", "2024-01-15")
        assert result == "kairos-prompt-user-email-com-2024-01-15"

    def test_preserves_alphanumeric_and_hyphens(self) -> None:
        """Should keep alphanumeric, hyphens, and underscores."""
        result = make_prompt_schedule_name("user_123-abc", "2024-01-15")
        assert result == "kairos-prompt-user_123-abc-2024-01-15"


class TestSchedulerClient:
    """Tests for SchedulerClient."""

    @pytest.fixture
    def mock_boto_client(self) -> MagicMock:
        """Create a mock boto3 scheduler client."""
        return MagicMock()

    @pytest.fixture
    def scheduler(self, mock_boto_client: MagicMock) -> SchedulerClient:
        """Create a SchedulerClient with mocked boto3."""
        with patch("boto3.client", return_value=mock_boto_client):
            client = SchedulerClient(region="eu-west-1")
            client.client = mock_boto_client
            return client

    def test_upsert_creates_schedule_when_not_exists(
        self, scheduler: SchedulerClient, mock_boto_client: MagicMock
    ) -> None:
        """Should create schedule if update fails with ResourceNotFoundException."""
        # First call (update) raises ResourceNotFoundException
        mock_boto_client.update_schedule.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "UpdateSchedule",
        )
        mock_boto_client.create_schedule.return_value = {"ScheduleArn": "arn:..."}

        result = scheduler.upsert_one_time_schedule(
            name="test-schedule",
            at_time_utc_iso="2024-01-15T17:30:00Z",
            target_arn="arn:aws:lambda:...",
            payload={"user_id": "user-001"},
            role_arn="arn:aws:iam::...",
        )

        mock_boto_client.update_schedule.assert_called_once()
        mock_boto_client.create_schedule.assert_called_once()
        assert result == {"ScheduleArn": "arn:..."}

    def test_upsert_updates_existing_schedule(
        self, scheduler: SchedulerClient, mock_boto_client: MagicMock
    ) -> None:
        """Should update schedule if it already exists."""
        mock_boto_client.update_schedule.return_value = {"ScheduleArn": "arn:..."}

        result = scheduler.upsert_one_time_schedule(
            name="test-schedule",
            at_time_utc_iso="2024-01-15T17:30:00Z",
            target_arn="arn:aws:lambda:...",
            payload={"user_id": "user-001"},
            role_arn="arn:aws:iam::...",
        )

        mock_boto_client.update_schedule.assert_called_once()
        mock_boto_client.create_schedule.assert_not_called()
        assert result == {"ScheduleArn": "arn:..."}

    def test_delete_schedule_success(
        self, scheduler: SchedulerClient, mock_boto_client: MagicMock
    ) -> None:
        """Should return True when schedule is deleted."""
        mock_boto_client.delete_schedule.return_value = {}

        result = scheduler.delete_schedule("test-schedule")

        assert result is True
        mock_boto_client.delete_schedule.assert_called_once_with(
            Name="test-schedule",
            GroupName="default",
        )

    def test_delete_schedule_not_found(
        self, scheduler: SchedulerClient, mock_boto_client: MagicMock
    ) -> None:
        """Should return True when schedule doesn't exist."""
        mock_boto_client.delete_schedule.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "DeleteSchedule",
        )

        result = scheduler.delete_schedule("test-schedule")

        assert result is True

    def test_get_schedule_returns_details(
        self, scheduler: SchedulerClient, mock_boto_client: MagicMock
    ) -> None:
        """Should return schedule details when found."""
        mock_boto_client.get_schedule.return_value = {
            "Name": "test-schedule",
            "ScheduleExpression": "at(2024-01-15T17:30:00)",
        }

        result = scheduler.get_schedule("test-schedule")

        assert result is not None
        assert result["Name"] == "test-schedule"

    def test_get_schedule_returns_none_when_not_found(
        self, scheduler: SchedulerClient, mock_boto_client: MagicMock
    ) -> None:
        """Should return None when schedule doesn't exist."""
        mock_boto_client.get_schedule.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "GetSchedule",
        )

        result = scheduler.get_schedule("test-schedule")

        assert result is None
