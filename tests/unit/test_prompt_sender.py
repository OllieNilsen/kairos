"""Unit tests for prompt sender handler."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from src.core.models import Meeting, UserState
from src.handlers.prompt_sender import (
    SMS_PROMPT_TEMPLATE,
    _build_sms_prompt,
    _handle_initial_prompt,
    _handle_retry,
    build_multi_meeting_prompt,
    handler,
)


class TestHandler:
    """Tests for the main handler function."""

    def test_routes_initial_prompt(self) -> None:
        """Should route non-retry to initial prompt handler."""
        with patch("src.handlers.prompt_sender._handle_initial_prompt") as mock_initial:
            mock_initial.return_value = {"statusCode": 200, "body": {"status": "ok"}}

            handler({"user_id": "user-001", "date": "2026-01-02"}, MagicMock())

            mock_initial.assert_called_once_with("user-001", "2026-01-02")

    def test_routes_retry(self) -> None:
        """Should route retry to retry handler."""
        with patch("src.handlers.prompt_sender._handle_retry") as mock_retry:
            mock_retry.return_value = {"statusCode": 200, "body": {"status": "ok"}}

            handler(
                {"user_id": "user-001", "date": "2026-01-02", "is_retry": True, "retry_number": 2},
                MagicMock(),
            )

            mock_retry.assert_called_once_with("user-001", "2026-01-02", 2)


class TestHandleInitialPrompt:
    """Tests for _handle_initial_prompt function."""

    def _mock_meeting(self, title: str = "Team Standup", duration: int = 30) -> MagicMock:
        """Create a mock meeting."""
        meeting = MagicMock(spec=Meeting)
        meeting.meeting_id = "meeting-123"
        meeting.title = title
        meeting.attendees = []
        meeting.attendee_names = []
        meeting.duration_minutes.return_value = duration
        return meeting

    @patch("src.handlers.prompt_sender._get_twilio_client")
    @patch("src.handlers.prompt_sender.MeetingsRepository")
    @patch("src.handlers.prompt_sender.UserStateRepository")
    @patch("src.handlers.prompt_sender.SMSSendDedup")
    def test_sends_sms_prompt(
        self,
        mock_dedup_cls: MagicMock,
        mock_user_repo_cls: MagicMock,
        mock_meetings_repo_cls: MagicMock,
        mock_twilio: MagicMock,
    ) -> None:
        """Should send SMS prompt and update user state."""
        # Setup
        mock_dedup = MagicMock()
        mock_dedup.try_send_daily_prompt.return_value = True
        mock_dedup_cls.return_value = mock_dedup

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = UserState(
            user_id="user-001", phone_number="+15551234567"
        )
        mock_user_repo.can_prompt.return_value = (True, "ok")
        mock_user_repo_cls.return_value = mock_user_repo

        mock_meetings_repo = MagicMock()
        mock_meetings_repo.get_pending_meetings.return_value = [self._mock_meeting()]
        mock_meetings_repo_cls.return_value = mock_meetings_repo

        mock_twilio.return_value.send_sms.return_value = "SM123456"

        # Execute
        result = _handle_initial_prompt("user-001", "2026-01-02")

        # Assert
        assert result["statusCode"] == 202
        assert result["body"]["status"] == "sms_sent"
        assert result["body"]["message_sid"] == "SM123456"
        mock_twilio.return_value.send_sms.assert_called_once()
        mock_user_repo.record_prompt_sent.assert_called_once()

    @patch("src.handlers.prompt_sender.SMSSendDedup")
    def test_deduplicates_sms(self, mock_dedup_cls: MagicMock) -> None:
        """Should not send duplicate SMS."""
        mock_dedup = MagicMock()
        mock_dedup.try_send_daily_prompt.return_value = False
        mock_dedup_cls.return_value = mock_dedup

        result = _handle_initial_prompt("user-001", "2026-01-02")

        assert result["statusCode"] == 200
        assert result["body"]["status"] == "already_sent"

    @patch("src.handlers.prompt_sender.MeetingsRepository")
    @patch("src.handlers.prompt_sender.UserStateRepository")
    @patch("src.handlers.prompt_sender.SMSSendDedup")
    def test_no_meetings_releases_lock(
        self,
        mock_dedup_cls: MagicMock,
        mock_user_repo_cls: MagicMock,
        mock_meetings_repo_cls: MagicMock,
    ) -> None:
        """Should release idempotency lock if no meetings."""
        mock_dedup = MagicMock()
        mock_dedup.try_send_daily_prompt.return_value = True
        mock_dedup_cls.return_value = mock_dedup

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = UserState(
            user_id="user-001", phone_number="+15551234567"
        )
        mock_user_repo.can_prompt.return_value = (True, "ok")
        mock_user_repo_cls.return_value = mock_user_repo

        mock_meetings_repo = MagicMock()
        mock_meetings_repo.get_pending_meetings.return_value = []
        mock_meetings_repo_cls.return_value = mock_meetings_repo

        result = _handle_initial_prompt("user-001", "2026-01-02")

        assert result["body"]["status"] == "no_meetings"
        mock_dedup.release_daily_prompt.assert_called_once()

    @patch("src.handlers.prompt_sender.UserStateRepository")
    @patch("src.handlers.prompt_sender.SMSSendDedup")
    def test_respects_user_stopped(
        self, mock_dedup_cls: MagicMock, mock_user_repo_cls: MagicMock
    ) -> None:
        """Should not send if user has stopped."""
        mock_dedup = MagicMock()
        mock_dedup.try_send_daily_prompt.return_value = True
        mock_dedup_cls.return_value = mock_dedup

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = UserState(user_id="user-001", stopped=True)
        mock_user_repo.can_prompt.return_value = (False, "stopped")
        mock_user_repo_cls.return_value = mock_user_repo

        result = _handle_initial_prompt("user-001", "2026-01-02")

        assert result["body"]["status"] == "stopped"


class TestHandleRetry:
    """Tests for _handle_retry function."""

    def _mock_meeting(self) -> MagicMock:
        meeting = MagicMock(spec=Meeting)
        meeting.meeting_id = "meeting-123"
        meeting.title = "Test Meeting"
        meeting.attendees = []
        meeting.attendee_names = []
        meeting.duration_minutes.return_value = 30
        return meeting

    @patch("src.handlers.prompt_sender.get_parameter")
    @patch("src.handlers.prompt_sender.BlandClient")
    @patch("src.handlers.prompt_sender.MeetingsRepository")
    @patch("src.handlers.prompt_sender.UserStateRepository")
    @patch("src.handlers.prompt_sender.CallRetryDedup")
    def test_initiates_call_on_retry(
        self,
        mock_dedup_cls: MagicMock,
        mock_user_repo_cls: MagicMock,
        mock_meetings_repo_cls: MagicMock,
        mock_bland_cls: MagicMock,
        mock_get_param: MagicMock,
    ) -> None:
        """Should directly initiate call for retry."""
        # Setup
        mock_dedup = MagicMock()
        mock_dedup.try_acquire.return_value = True
        mock_dedup_cls.return_value = mock_dedup

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = UserState(
            user_id="user-001", phone_number="+15551234567"
        )
        mock_user_repo_cls.return_value = mock_user_repo

        mock_meetings_repo = MagicMock()
        mock_meetings_repo.get_pending_meetings.return_value = [self._mock_meeting()]
        mock_meetings_repo_cls.return_value = mock_meetings_repo

        mock_get_param.return_value = "test-api-key"

        async def mock_call(*args: Any, **kwargs: Any) -> str:
            return "call-456"

        mock_bland_cls.return_value.initiate_call_raw = mock_call

        # Execute
        result = _handle_retry("user-001", "2026-01-02", 1)

        # Assert
        assert result["statusCode"] == 202
        assert result["body"]["status"] == "call_initiated"
        assert result["body"]["call_id"] == "call-456"
        assert result["body"]["retry_number"] == 1

    @patch("src.handlers.prompt_sender.CallRetryDedup")
    def test_deduplicates_retry(self, mock_dedup_cls: MagicMock) -> None:
        """Should not execute duplicate retry."""
        mock_dedup = MagicMock()
        mock_dedup.try_acquire.return_value = False
        mock_dedup_cls.return_value = mock_dedup

        result = _handle_retry("user-001", "2026-01-02", 1)

        assert result["body"]["status"] == "retry_already_executed"

    @patch("src.handlers.prompt_sender.UserStateRepository")
    @patch("src.handlers.prompt_sender.CallRetryDedup")
    def test_skips_if_call_successful(
        self, mock_dedup_cls: MagicMock, mock_user_repo_cls: MagicMock
    ) -> None:
        """Should skip retry if call already successful."""
        mock_dedup = MagicMock()
        mock_dedup.try_acquire.return_value = True
        mock_dedup_cls.return_value = mock_dedup

        mock_user_repo = MagicMock()
        mock_user_repo.get_user_state.return_value = UserState(
            user_id="user-001", call_successful=True
        )
        mock_user_repo_cls.return_value = mock_user_repo

        result = _handle_retry("user-001", "2026-01-02", 1)

        assert result["body"]["status"] == "call_already_successful"


class TestBuildSmsPrompt:
    """Tests for _build_sms_prompt function."""

    def _mock_meeting(self, title: str, duration: int) -> MagicMock:
        meeting = MagicMock(spec=Meeting)
        meeting.title = title
        meeting.duration_minutes.return_value = duration
        return meeting

    def test_single_meeting(self) -> None:
        """Should format single meeting correctly."""
        meetings = [self._mock_meeting("Team Standup", 30)]
        result = _build_sms_prompt(meetings)

        assert "1 meeting to debrief" in result
        assert "Team Standup" in result
        assert "30min" in result
        assert "YES" in result
        assert "NO" in result

    def test_multiple_meetings(self) -> None:
        """Should format multiple meetings correctly."""
        meetings = [
            self._mock_meeting("Standup", 15),
            self._mock_meeting("Sprint Review", 60),
        ]
        result = _build_sms_prompt(meetings)

        assert "2 meetings to debrief" in result
        assert "Standup" in result
        assert "Sprint Review" in result

    def test_truncates_at_3_meetings(self) -> None:
        """Should show '...and N more' for >3 meetings."""
        meetings = [
            self._mock_meeting("Meeting 1", 30),
            self._mock_meeting("Meeting 2", 30),
            self._mock_meeting("Meeting 3", 30),
            self._mock_meeting("Meeting 4", 30),
            self._mock_meeting("Meeting 5", 30),
        ]
        result = _build_sms_prompt(meetings)

        assert "5 meetings to debrief" in result
        assert "Meeting 1" in result
        assert "Meeting 2" in result
        assert "Meeting 3" in result
        assert "Meeting 4" not in result
        assert "...and 2 more" in result


class TestBuildMultiMeetingPrompt:
    """Tests for build_multi_meeting_prompt function."""

    def _mock_meeting(self, title: str, duration: int = 30) -> MagicMock:
        meeting = MagicMock(spec=Meeting)
        meeting.title = title
        meeting.attendees = []
        meeting.attendee_names = []
        meeting.duration_minutes.return_value = duration
        return meeting

    def test_includes_all_meetings(self) -> None:
        """Should include all meetings in prompt."""
        meetings = [
            self._mock_meeting("Standup", 15),
            self._mock_meeting("Sprint Review", 60),
        ]
        result = build_multi_meeting_prompt(meetings)

        assert "1. Standup" in result
        assert "2. Sprint Review" in result
        assert "15 min" in result
        assert "60 min" in result

    def test_includes_attendee_names(self) -> None:
        """Should include attendee names if present."""
        meeting = self._mock_meeting("Team Sync", 30)
        meeting.attendees = [{"email": "alice@test.com"}]
        meeting.attendee_names = ["Alice", "Bob", "Charlie"]

        result = build_multi_meeting_prompt([meeting])

        assert "Alice" in result
        assert "Bob" in result
        assert "Charlie" in result

    def test_includes_debrief_instructions(self) -> None:
        """Should include debrief instructions."""
        result = build_multi_meeting_prompt([self._mock_meeting("Test", 30)])

        assert "outcomes" in result.lower()
        assert "action items" in result.lower()
        assert "5 minutes" in result


class TestSmsPromptTemplate:
    """Tests for SMS_PROMPT_TEMPLATE constant."""

    def test_template_has_placeholders(self) -> None:
        """Template should have required placeholders."""
        assert "{count}" in SMS_PROMPT_TEMPLATE
        assert "{meetings}" in SMS_PROMPT_TEMPLATE
        assert "{s}" in SMS_PROMPT_TEMPLATE

    def test_template_has_response_options(self) -> None:
        """Template should mention YES and NO."""
        assert "YES" in SMS_PROMPT_TEMPLATE
        assert "NO" in SMS_PROMPT_TEMPLATE
