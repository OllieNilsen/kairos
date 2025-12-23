"""Unit tests for idempotency helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.adapters.idempotency import (
    CallBatchDedup,
    CallRetryDedup,
    DailyLease,
    IdempotencyStore,
    InboundSMSDedup,
    SMSSendDedup,
)


class TestIdempotencyStore:
    """Tests for the base IdempotencyStore class."""

    @pytest.fixture
    def mock_table(self) -> MagicMock:
        """Create a mock DynamoDB table."""
        return MagicMock()

    @pytest.fixture
    def store(self, mock_table: MagicMock) -> IdempotencyStore:
        """Create an IdempotencyStore with mocked DynamoDB."""
        with patch("boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_table
            s = IdempotencyStore("test-table")
            s.table = mock_table
            return s

    def test_try_acquire_succeeds_on_first_call(
        self, store: IdempotencyStore, mock_table: MagicMock
    ) -> None:
        """Should return True when key doesn't exist."""
        mock_table.put_item.return_value = {}

        result = store.try_acquire("test-key")

        assert result is True
        mock_table.put_item.assert_called_once()

    def test_try_acquire_fails_on_duplicate(
        self, store: IdempotencyStore, mock_table: MagicMock
    ) -> None:
        """Should return False when key already exists."""
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "PutItem",
        )

        result = store.try_acquire("test-key")

        assert result is False

    def test_try_acquire_raises_on_other_error(
        self, store: IdempotencyStore, mock_table: MagicMock
    ) -> None:
        """Should raise on non-conditional-check errors."""
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError"}},
            "PutItem",
        )

        with pytest.raises(ClientError):
            store.try_acquire("test-key")

    def test_release_deletes_key(self, store: IdempotencyStore, mock_table: MagicMock) -> None:
        """Should delete the idempotency key."""
        store.release("test-key")

        mock_table.delete_item.assert_called_once_with(Key={"idempotency_key": "test-key"})


class TestSMSSendDedup:
    """Tests for SMS send deduplication."""

    def test_make_key_format(self) -> None:
        """Should generate sms-send:{user_id}#{date} format."""
        key = SMSSendDedup.make_key("user-001", "2024-01-15")
        assert key == "sms-send:user-001#2024-01-15"

    def test_try_send_daily_prompt(self) -> None:
        """Should call try_acquire with correct key."""
        with patch("boto3.resource"):
            dedup = SMSSendDedup("test-table")
            dedup.try_acquire = MagicMock(return_value=True)

            result = dedup.try_send_daily_prompt("user-001", "2024-01-15")

            assert result is True
            dedup.try_acquire.assert_called_once_with(
                "sms-send:user-001#2024-01-15",
                {"type": "daily_prompt"},
            )


class TestInboundSMSDedup:
    """Tests for inbound SMS deduplication."""

    def test_make_key_format(self) -> None:
        """Should generate sms-in:{message_sid} format."""
        key = InboundSMSDedup.make_key("SM123abc")
        assert key == "sms-in:SM123abc"


class TestCallBatchDedup:
    """Tests for call batch deduplication."""

    def test_make_key_format(self) -> None:
        """Should generate call-batch:{user_id}#{date} format."""
        key = CallBatchDedup.make_key("user-001", "2024-01-15")
        assert key == "call-batch:user-001#2024-01-15"


class TestCallRetryDedup:
    """Tests for call retry deduplication."""

    def test_make_key_format(self) -> None:
        """Should generate call-retry:{user_id}#{date}#{retry_number} format."""
        key = CallRetryDedup.make_key("user-001", "2024-01-15", 1)
        assert key == "call-retry:user-001#2024-01-15#1"

    def test_make_key_format_different_retry_numbers(self) -> None:
        """Should include retry number in key."""
        key1 = CallRetryDedup.make_key("user-001", "2024-01-15", 1)
        key2 = CallRetryDedup.make_key("user-001", "2024-01-15", 2)
        key3 = CallRetryDedup.make_key("user-001", "2024-01-15", 3)

        assert key1 == "call-retry:user-001#2024-01-15#1"
        assert key2 == "call-retry:user-001#2024-01-15#2"
        assert key3 == "call-retry:user-001#2024-01-15#3"

    def test_try_schedule_retry(self) -> None:
        """Should call try_acquire with correct key and metadata."""
        with patch("boto3.resource"):
            dedup = CallRetryDedup("test-table")
            dedup.try_acquire = MagicMock(return_value=True)

            result = dedup.try_schedule_retry("user-001", "2024-01-15", 2)

            assert result is True
            dedup.try_acquire.assert_called_once_with(
                "call-retry:user-001#2024-01-15#2",
                {"type": "call_retry", "retry_number": 2},
            )

    def test_release_retry(self) -> None:
        """Should call release with correct key."""
        with patch("boto3.resource"):
            dedup = CallRetryDedup("test-table")
            dedup.release = MagicMock()

            dedup.release_retry("user-001", "2024-01-15", 1)

            dedup.release.assert_called_once_with("call-retry:user-001#2024-01-15#1")


class TestDailyLease:
    """Tests for daily lease mechanism."""

    def test_make_key_format(self) -> None:
        """Should generate {operation}:{user_id}#{date} format."""
        key = DailyLease.make_key("daily-plan", "user-001", "2024-01-15")
        assert key == "daily-plan:user-001#2024-01-15"

    @pytest.fixture
    def mock_table(self) -> MagicMock:
        """Create a mock DynamoDB table."""
        return MagicMock()

    @pytest.fixture
    def lease(self, mock_table: MagicMock) -> DailyLease:
        """Create a DailyLease with mocked DynamoDB."""
        with patch("boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_table
            dl = DailyLease("test-table")
            dl.table = mock_table
            return dl

    def test_try_acquire_succeeds(self, lease: DailyLease, mock_table: MagicMock) -> None:
        """Should return True when lease acquired."""
        mock_table.put_item.return_value = {}

        # DailyLease.try_acquire takes lease_key and owner
        lease_key = DailyLease.make_key("daily-plan", "user-001", "2024-01-15")
        result = lease.try_acquire(lease_key, "lambda-request-id")

        assert result is True

    def test_try_acquire_fails_when_held(self, lease: DailyLease, mock_table: MagicMock) -> None:
        """Should return False when lease already held."""
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "PutItem",
        )

        lease_key = DailyLease.make_key("daily-plan", "user-001", "2024-01-15")
        result = lease.try_acquire(lease_key, "lambda-request-id")

        assert result is False
