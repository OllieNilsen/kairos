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

    def test_record_call_success(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should set call_successful to True."""
        repo.record_call_success("user-001")

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args
        assert call_args[1]["Key"] == {"user_id": "user-001"}
        expr_values = call_args[1]["ExpressionAttributeValues"]
        assert expr_values[":true"] is True

    def test_record_retry_scheduled(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should update retry state fields."""
        repo.record_retry_scheduled(
            user_id="user-001",
            next_retry_at="2024-01-15T18:00:00Z",
            retry_schedule_name="kairos-retry-user-001-2024-01-15-1",
        )

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args
        expr_values = call_args[1]["ExpressionAttributeValues"]
        assert expr_values[":one"] == 1
        assert expr_values[":next_retry"] == "2024-01-15T18:00:00Z"
        assert expr_values[":schedule_name"] == "kairos-retry-user-001-2024-01-15-1"

    def test_clear_retry_schedule(
        self, repo: UserStateRepository, mock_table: MagicMock
    ) -> None:
        """Should clear retry schedule fields."""
        repo.clear_retry_schedule("user-001")

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args
        assert call_args[1]["Key"] == {"user_id": "user-001"}
        expr_values = call_args[1]["ExpressionAttributeValues"]
        assert expr_values[":null"] is None

    def test_can_retry_returns_true_when_allowed(
        self, repo: UserStateRepository
    ) -> None:
        """Should return True when retry is allowed."""
        state = UserState(
            user_id="user-001",
            daily_call_made=True,
            call_successful=False,
            retries_today=1,
        )

        can_retry, reason = repo.can_retry(state, max_retries=3)

        assert can_retry is True
        assert reason == "ok"

    def test_can_retry_returns_false_when_call_successful(
        self, repo: UserStateRepository
    ) -> None:
        """Should return False when call already successful."""
        state = UserState(
            user_id="user-001",
            call_successful=True,
            retries_today=0,
        )

        can_retry, reason = repo.can_retry(state, max_retries=3)

        assert can_retry is False
        assert reason == "call_already_successful"

    def test_can_retry_returns_false_when_max_reached(
        self, repo: UserStateRepository
    ) -> None:
        """Should return False when max retries reached."""
        state = UserState(
            user_id="user-001",
            call_successful=False,
            retries_today=3,
        )

        can_retry, reason = repo.can_retry(state, max_retries=3)

        assert can_retry is False
        assert reason == "max_retries_reached"

    def test_can_retry_returns_false_when_stopped(
        self, repo: UserStateRepository
    ) -> None:
        """Should return False when user is stopped."""
        state = UserState(
            user_id="user-001",
            stopped=True,
            retries_today=0,
        )

        can_retry, reason = repo.can_retry(state, max_retries=3)

        assert can_retry is False
        assert reason == "stopped"


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
        assert state.call_successful is False
        assert state.retries_today == 0
        assert state.next_retry_at is None
        assert state.retry_schedule_name is None

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
