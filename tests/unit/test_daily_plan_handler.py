"""Unit tests for daily plan prompt handler."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.core.models import UserState


class TestDailyPlanHandler:
    """Tests for daily plan Lambda handler."""

    @pytest.fixture
    def mock_env(self) -> dict[str, str]:
        """Environment variables for testing."""
        return {
            "USER_STATE_TABLE": "kairos-user-state",
            "IDEMPOTENCY_TABLE": "kairos-idempotency",
            "PROMPT_SENDER_ARN": "arn:aws:lambda:eu-west-1:123456789:function:prompt-sender",
            "SCHEDULER_ROLE_ARN": "arn:aws:iam::123456789:role/scheduler-role",
            "AWS_REGION": "eu-west-1",
            "MVP_USER_ID": "user-001",
        }

    @pytest.fixture
    def sample_user_state(self) -> UserState:
        """Create sample user state."""
        return UserState(
            user_id="user-001",
            timezone="Europe/London",
            preferred_prompt_time="17:30",
            stopped=False,
        )

    def test_already_planned_returns_200(self, mock_env: dict[str, str]) -> None:
        """Should return 200 if daily plan already executed."""
        from src.handlers.daily_plan_prompt import handler

        mock_lease = MagicMock()
        mock_lease.try_acquire.return_value = False

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.daily_plan_prompt.DailyLease", return_value=mock_lease),
        ):
            response = handler({}, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "already_planned"

    def test_user_stopped_returns_200(
        self, mock_env: dict[str, str], sample_user_state: UserState
    ) -> None:
        """Should return 200 if user has stopped."""
        from src.handlers.daily_plan_prompt import handler

        sample_user_state.stopped = True

        mock_lease = MagicMock()
        mock_lease.try_acquire.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = sample_user_state

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.daily_plan_prompt.DailyLease", return_value=mock_lease),
            patch(
                "src.handlers.daily_plan_prompt.UserStateRepository",
                return_value=mock_user_repo,
            ),
        ):
            response = handler({}, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "user_stopped"

    def test_successful_plan_creates_event_and_schedule(
        self, mock_env: dict[str, str], sample_user_state: UserState
    ) -> None:
        """Should create calendar event and schedule on success."""
        from src.handlers.daily_plan_prompt import handler

        mock_lease = MagicMock()
        mock_lease.try_acquire.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = sample_user_state

        mock_calendar = MagicMock()
        mock_calendar.get_event.side_effect = Exception("Not found")
        mock_calendar.create_event.return_value = {"id": "event-123", "etag": "etag-123"}

        mock_scheduler = MagicMock()

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.daily_plan_prompt.DailyLease", return_value=mock_lease),
            patch(
                "src.handlers.daily_plan_prompt.UserStateRepository",
                return_value=mock_user_repo,
            ),
            patch("src.handlers.daily_plan_prompt.GoogleCalendarClient") as mock_cal_class,
            patch(
                "src.handlers.daily_plan_prompt.SchedulerClient",
                return_value=mock_scheduler,
            ),
        ):
            mock_cal_class.from_ssm.return_value = mock_calendar
            response = handler({}, MagicMock())

        assert response["statusCode"] == 200
        assert response["body"]["status"] == "planned"
        mock_calendar.create_event.assert_called_once()
        mock_scheduler.upsert_one_time_schedule.assert_called_once()
        mock_user_repo.reset_daily_state.assert_called_once()

    def test_uses_default_prompt_time_when_not_set(self, mock_env: dict[str, str]) -> None:
        """Should use default prompt time when user state is None."""
        from src.handlers.daily_plan_prompt import handler

        mock_lease = MagicMock()
        mock_lease.try_acquire.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = None

        mock_calendar = MagicMock()
        mock_calendar.get_event.side_effect = Exception("Not found")
        mock_calendar.create_event.return_value = {"id": "event-123", "etag": "etag-123"}

        mock_scheduler = MagicMock()

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.daily_plan_prompt.DailyLease", return_value=mock_lease),
            patch(
                "src.handlers.daily_plan_prompt.UserStateRepository",
                return_value=mock_user_repo,
            ),
            patch("src.handlers.daily_plan_prompt.GoogleCalendarClient") as mock_cal_class,
            patch(
                "src.handlers.daily_plan_prompt.SchedulerClient",
                return_value=mock_scheduler,
            ),
        ):
            mock_cal_class.from_ssm.return_value = mock_calendar
            response = handler({}, MagicMock())

        assert response["statusCode"] == 200
        # Check that it created event at 17:30 (default time)
        create_args = mock_calendar.create_event.call_args
        start_time = create_args[1]["start_time"]
        assert start_time.hour == 17
        assert start_time.minute == 30

    def test_updates_existing_event_for_today(
        self, mock_env: dict[str, str], sample_user_state: UserState
    ) -> None:
        """Should update existing event if for today."""
        from src.handlers.daily_plan_prompt import handler

        sample_user_state.debrief_event_id = "existing-event-123"

        # Get today's date in the right timezone
        tz = ZoneInfo("Europe/London")
        today_str = datetime.now(tz).strftime("%Y-%m-%d")

        mock_lease = MagicMock()
        mock_lease.try_acquire.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = sample_user_state

        mock_calendar = MagicMock()
        mock_calendar.get_event.return_value = {
            "id": "existing-event-123",
            "extendedProperties": {"private": {"kairos_date": today_str}},
        }
        mock_calendar.update_event.return_value = {
            "id": "existing-event-123",
            "etag": "new-etag",
        }

        mock_scheduler = MagicMock()

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.daily_plan_prompt.DailyLease", return_value=mock_lease),
            patch(
                "src.handlers.daily_plan_prompt.UserStateRepository",
                return_value=mock_user_repo,
            ),
            patch("src.handlers.daily_plan_prompt.GoogleCalendarClient") as mock_cal_class,
            patch(
                "src.handlers.daily_plan_prompt.SchedulerClient",
                return_value=mock_scheduler,
            ),
        ):
            mock_cal_class.from_ssm.return_value = mock_calendar
            response = handler({}, MagicMock())

        assert response["statusCode"] == 200
        mock_calendar.update_event.assert_called_once()
        mock_calendar.create_event.assert_not_called()

    def test_cleans_up_old_schedules(
        self, mock_env: dict[str, str], sample_user_state: UserState
    ) -> None:
        """Should delete yesterday's schedule."""
        from src.handlers.daily_plan_prompt import handler

        mock_lease = MagicMock()
        mock_lease.try_acquire.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = sample_user_state

        mock_calendar = MagicMock()
        mock_calendar.get_event.side_effect = Exception("Not found")
        mock_calendar.create_event.return_value = {"id": "event-123", "etag": "etag-123"}

        mock_scheduler = MagicMock()

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.daily_plan_prompt.DailyLease", return_value=mock_lease),
            patch(
                "src.handlers.daily_plan_prompt.UserStateRepository",
                return_value=mock_user_repo,
            ),
            patch("src.handlers.daily_plan_prompt.GoogleCalendarClient") as mock_cal_class,
            patch(
                "src.handlers.daily_plan_prompt.SchedulerClient",
                return_value=mock_scheduler,
            ),
        ):
            mock_cal_class.from_ssm.return_value = mock_calendar
            handler({}, MagicMock())

        # Check delete_schedule was called for cleanup
        mock_scheduler.delete_schedule.assert_called_once()

    def test_releases_lease_on_failure(self, mock_env: dict[str, str]) -> None:
        """Should release lease when planning fails."""
        from src.handlers.daily_plan_prompt import handler

        mock_lease = MagicMock()
        mock_lease.try_acquire.return_value = True

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.side_effect = Exception("DB error")

        with (
            patch.dict("os.environ", mock_env),
            patch("src.handlers.daily_plan_prompt.DailyLease", return_value=mock_lease),
            patch(
                "src.handlers.daily_plan_prompt.UserStateRepository",
                return_value=mock_user_repo,
            ),
            pytest.raises(Exception, match="DB error"),
        ):
            handler({}, MagicMock())

        mock_lease.release.assert_called_once()
