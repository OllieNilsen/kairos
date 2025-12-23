"""Unit tests for user state repository."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.adapters.user_state import UserStateRepository
from src.core.models import UserState


class TestUserStateRepository:
    """Tests for UserStateRepository."""

    @pytest.fixture
    def mock_table(self) -> MagicMock:
        """Create a mock DynamoDB table."""
        return MagicMock()

    @pytest.fixture
    def repo(self, mock_table: MagicMock) -> UserStateRepository:
        """Create a UserStateRepository with mocked DynamoDB."""
        with patch("boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_table
            r = UserStateRepository("test-table")
            r.table = mock_table
            return r

    def test_get_user_state_returns_state_when_found(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should return UserState when item exists."""
        mock_table.get_item.return_value = {
            "Item": {
                "user_id": "user-001",
                "phone_number": "+447123456789",
                "timezone": "Europe/London",
                "preferred_prompt_time": "17:30",
                "stopped": False,
                "prompts_sent_today": 0,
            }
        }

        result = repo.get_user_state("user-001")

        assert result is not None
        assert result.user_id == "user-001"
        assert result.phone_number == "+447123456789"
        assert result.timezone == "Europe/London"

    def test_get_user_state_returns_none_when_not_found(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should return None when item doesn't exist."""
        mock_table.get_item.return_value = {}

        result = repo.get_user_state("user-001")

        assert result is None

    def test_save_user_state_puts_item(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should put item to DynamoDB."""
        state = UserState(
            user_id="user-001",
            phone_number="+447123456789",
            timezone="Europe/London",
        )

        repo.save_user_state(state)

        mock_table.put_item.assert_called_once()
        call_args = mock_table.put_item.call_args
        assert call_args[1]["Item"]["user_id"] == "user-001"
        assert call_args[1]["Item"]["phone_number"] == "+447123456789"

    def test_reset_daily_state_updates_counters(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should reset daily counters."""
        repo.reset_daily_state(
            user_id="user-001",
            next_prompt_at="2024-01-15T17:30:00Z",
            prompt_schedule_name="kairos-prompt-user-001-2024-01-15",
            debrief_event_id="event123",
        )

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args
        assert call_args[1]["Key"] == {"user_id": "user-001"}
        expr_values = call_args[1]["ExpressionAttributeValues"]
        assert expr_values[":zero"] == 0
        assert expr_values[":false"] is False
        assert expr_values[":next_prompt"] == "2024-01-15T17:30:00Z"

    def test_record_prompt_sent_success(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should return True when prompt recorded successfully."""
        mock_table.update_item.return_value = {}

        result = repo.record_prompt_sent("user-001", "user-001#2024-01-15")

        assert result is True
        mock_table.update_item.assert_called_once()

    def test_record_prompt_sent_already_sent(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should return False when prompt already sent."""
        mock_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "UpdateItem",
        )

        result = repo.record_prompt_sent("user-001", "user-001#2024-01-15")

        assert result is False

    def test_record_call_initiated(self, repo: UserStateRepository, mock_table: MagicMock) -> None:
        """Should update daily_call_made to True."""
        repo.record_call_initiated("user-001", "user-001#2024-01-15")

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args
        expr_values = call_args[1]["ExpressionAttributeValues"]
        assert expr_values[":true"] is True

    def test_set_snooze(self, repo: UserStateRepository, mock_table: MagicMock) -> None:
        """Should set snooze_until."""
        repo.set_snooze("user-001", "2024-01-16T08:00:00Z")

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args
        expr_values = call_args[1]["ExpressionAttributeValues"]
        assert expr_values[":until"] == "2024-01-16T08:00:00Z"

    def test_clear_snooze(self, repo: UserStateRepository, mock_table: MagicMock) -> None:
        """Should clear snooze_until."""
        repo.clear_snooze("user-001")

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args
        expr_values = call_args[1]["ExpressionAttributeValues"]
        assert expr_values[":null"] is None

    def test_set_stop(self, repo: UserStateRepository, mock_table: MagicMock) -> None:
        """Should set stopped to True."""
        repo.set_stop("user-001", stop=True)

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args
        expr_values = call_args[1]["ExpressionAttributeValues"]
        assert expr_values[":stop"] is True


class TestUserStateModel:
    """Tests for UserState Pydantic model."""

    def test_default_values(self) -> None:
        """Should have sensible defaults."""
        state = UserState(user_id="user-001")

        assert state.timezone == "Europe/London"
        assert state.preferred_prompt_time == "17:30"
        assert state.prompts_sent_today == 0
        assert state.stopped is False
        assert state.daily_call_made is False

    def test_all_fields_set(self) -> None:
        """Should accept all fields."""
        state = UserState(
            user_id="user-001",
            phone_number="+447123456789",
            email="test@example.com",
            timezone="America/New_York",
            preferred_prompt_time="18:00",
            next_prompt_at="2024-01-15T23:00:00Z",
            prompts_sent_today=1,
            daily_call_made=True,
            stopped=False,
        )

        assert state.user_id == "user-001"
        assert state.phone_number == "+447123456789"
        assert state.timezone == "America/New_York"
        assert state.prompts_sent_today == 1
        assert state.daily_call_made is True
