"""Unit tests for CalendarSyncStateRepository (Slice 4B - Webhook routing)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.core.models import CalendarSyncState


class TestCalendarSyncStateRepository:
    """Tests for CalendarSyncStateRepository channel/subscription routing."""

    @pytest.fixture
    def google_sync_state(self) -> CalendarSyncState:
        """Create Google sync state with channel token."""
        return CalendarSyncState(
            user_id="user-001",
            provider="google",
            provider_calendar_id="primary",
            subscription_id="channel-abc123",
            subscription_expiry=datetime.now(UTC) + timedelta(days=7),
            channel_token=secrets.token_urlsafe(32),
            last_sync_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    @pytest.fixture
    def microsoft_sync_state(self) -> CalendarSyncState:
        """Create Microsoft sync state with client state."""
        return CalendarSyncState(
            user_id="user-002",
            provider="microsoft",
            provider_calendar_id="user002@example.com",
            subscription_id="sub-xyz789",
            subscription_expiry=datetime.now(UTC) + timedelta(days=3),
            client_state=str(uuid.uuid4()),
            last_sync_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def test_save_google_sync_state_creates_route_item(
        self, google_sync_state: CalendarSyncState
    ) -> None:
        """Should create SYNC item and GOOGLE#CHANNEL# routing item transactionally."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_dynamodb = MagicMock()
        repo = CalendarSyncStateRepository("test-table", dynamodb=mock_dynamodb)

        repo.save_sync_state(google_sync_state)

        # Should write 2 items: SYNC + GOOGLE#CHANNEL# route
        mock_dynamodb.transact_write_items.assert_called_once()
        call_args = mock_dynamodb.transact_write_items.call_args[1]
        items = call_args["TransactItems"]
        assert len(items) == 2

        # Verify SYNC item
        sync_item = items[0]["Put"]
        assert sync_item["Item"]["pk"]["S"] == "USER#user-001#PROVIDER#google"
        assert sync_item["Item"]["sk"]["S"] == "SYNC"

        # Verify Google channel route item
        route_item = items[1]["Put"]
        assert route_item["Item"]["pk"]["S"] == "GOOGLE#CHANNEL#channel-abc123"
        assert route_item["Item"]["sk"]["S"] == "ROUTE"
        assert route_item["Item"]["user_id"]["S"] == "user-001"
        assert route_item["Item"]["channel_token"]["S"] == google_sync_state.channel_token

    def test_save_microsoft_sync_state_creates_route_item(
        self, microsoft_sync_state: CalendarSyncState
    ) -> None:
        """Should create SYNC item and MS#SUB# routing item transactionally."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_dynamodb = MagicMock()
        repo = CalendarSyncStateRepository("test-table", dynamodb=mock_dynamodb)

        repo.save_sync_state(microsoft_sync_state)

        mock_dynamodb.transact_write_items.assert_called_once()
        call_args = mock_dynamodb.transact_write_items.call_args[1]
        items = call_args["TransactItems"]
        assert len(items) == 2

        # Verify MS subscription route item
        route_item = items[1]["Put"]
        assert route_item["Item"]["pk"]["S"] == "MS#SUB#sub-xyz789"
        assert route_item["Item"]["sk"]["S"] == "ROUTE"
        assert route_item["Item"]["user_id"]["S"] == "user-002"
        assert route_item["Item"]["client_state"]["S"] == microsoft_sync_state.client_state

    def test_get_by_google_channel_id(self, google_sync_state: CalendarSyncState) -> None:
        """Should lookup user_id and channel_token by channel_id (O(1) GetItem)."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "GOOGLE#CHANNEL#channel-abc123",
                "sk": "ROUTE",
                "user_id": "user-001",
                "provider": "google",
                "provider_calendar_id": "primary",
                "channel_token": google_sync_state.channel_token,
                "channel_expiry": google_sync_state.subscription_expiry.isoformat(),
            }
        }

        repo = CalendarSyncStateRepository("test-table", table=mock_table)
        result = repo.get_by_google_channel_id("channel-abc123")

        assert result is not None
        assert result["user_id"] == "user-001"
        assert result["channel_token"] == google_sync_state.channel_token

        # Verify O(1) lookup
        mock_table.get_item.assert_called_once_with(
            Key={"pk": "GOOGLE#CHANNEL#channel-abc123", "sk": "ROUTE"}
        )

    def test_get_by_google_channel_id_not_found(self) -> None:
        """Should return None if channel not found."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        repo = CalendarSyncStateRepository("test-table", table=mock_table)
        result = repo.get_by_google_channel_id("invalid-channel")

        assert result is None

    def test_get_by_microsoft_subscription_id(
        self, microsoft_sync_state: CalendarSyncState
    ) -> None:
        """Should lookup user_id and client_state by subscription_id (O(1) GetItem)."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "MS#SUB#sub-xyz789",
                "sk": "ROUTE",
                "user_id": "user-002",
                "provider": "microsoft",
                "client_state": microsoft_sync_state.client_state,
                "subscription_expiry": microsoft_sync_state.subscription_expiry.isoformat(),
            }
        }

        repo = CalendarSyncStateRepository("test-table", table=mock_table)
        result = repo.get_by_microsoft_subscription_id("sub-xyz789")

        assert result is not None
        assert result["user_id"] == "user-002"
        assert result["client_state"] == microsoft_sync_state.client_state

    def test_verify_google_channel_token_success(
        self, google_sync_state: CalendarSyncState
    ) -> None:
        """Should verify Google channel token using constant-time comparison."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "GOOGLE#CHANNEL#channel-abc123",
                "sk": "ROUTE",
                "user_id": "user-001",
                "channel_token": google_sync_state.channel_token,
            }
        }

        repo = CalendarSyncStateRepository("test-table", table=mock_table)
        is_valid = repo.verify_google_channel_token(
            "channel-abc123", google_sync_state.channel_token
        )

        assert is_valid is True

    def test_verify_google_channel_token_invalid(
        self, google_sync_state: CalendarSyncState
    ) -> None:
        """Should reject invalid Google channel token."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "GOOGLE#CHANNEL#channel-abc123",
                "sk": "ROUTE",
                "user_id": "user-001",
                "channel_token": google_sync_state.channel_token,
            }
        }

        repo = CalendarSyncStateRepository("test-table", table=mock_table)
        is_valid = repo.verify_google_channel_token("channel-abc123", "WRONG_TOKEN")

        assert is_valid is False

    def test_verify_microsoft_client_state_success(
        self, microsoft_sync_state: CalendarSyncState
    ) -> None:
        """Should verify Microsoft client state (current or previous within overlap)."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "MS#SUB#sub-xyz789",
                "sk": "ROUTE",
                "user_id": "user-002",
                "client_state": microsoft_sync_state.client_state,
                "previous_client_state": None,
            }
        }

        repo = CalendarSyncStateRepository("test-table", table=mock_table)
        is_valid = repo.verify_microsoft_client_state(
            "sub-xyz789", microsoft_sync_state.client_state
        )

        assert is_valid is True

    def test_verify_microsoft_client_state_accepts_previous_within_overlap(
        self,
    ) -> None:
        """Should accept previous client_state during rotation overlap window (60 min)."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        previous_client_state = str(uuid.uuid4())
        current_client_state = str(uuid.uuid4())
        overlap_expires = datetime.now(UTC) + timedelta(minutes=30)  # Still valid

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "MS#SUB#sub-xyz789",
                "sk": "ROUTE",
                "user_id": "user-002",
                "client_state": current_client_state,
                "previous_client_state": previous_client_state,
                "previous_client_state_expires": overlap_expires.isoformat(),
            }
        }

        repo = CalendarSyncStateRepository("test-table", table=mock_table)

        # Both current and previous should be accepted
        assert repo.verify_microsoft_client_state("sub-xyz789", current_client_state) is True
        assert repo.verify_microsoft_client_state("sub-xyz789", previous_client_state) is True

    def test_verify_microsoft_client_state_rejects_expired_previous(self) -> None:
        """Should reject previous client_state after overlap window expires."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        previous_client_state = str(uuid.uuid4())
        current_client_state = str(uuid.uuid4())
        overlap_expires = datetime.now(UTC) - timedelta(minutes=5)  # Expired

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "MS#SUB#sub-xyz789",
                "sk": "ROUTE",
                "user_id": "user-002",
                "client_state": current_client_state,
                "previous_client_state": previous_client_state,
                "previous_client_state_expires": overlap_expires.isoformat(),
            }
        }

        repo = CalendarSyncStateRepository("test-table", table=mock_table)

        # Current should still be accepted
        assert repo.verify_microsoft_client_state("sub-xyz789", current_client_state) is True

        # Previous should be rejected (expired)
        assert repo.verify_microsoft_client_state("sub-xyz789", previous_client_state) is False

    def test_get_sync_state(self, google_sync_state: CalendarSyncState) -> None:
        """Should fetch full sync state for user/provider."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "USER#user-001#PROVIDER#google",
                "sk": "SYNC",
                "user_id": "user-001",
                "provider": "google",
                "provider_calendar_id": "primary",
                "subscription_id": "channel-abc123",
                "subscription_expiry": google_sync_state.subscription_expiry.isoformat(),
                "channel_token": google_sync_state.channel_token,
                "sync_token": "sync_token_abc",
                "last_sync_at": google_sync_state.last_sync_at.isoformat(),
                "created_at": google_sync_state.created_at.isoformat(),
                "updated_at": google_sync_state.updated_at.isoformat(),
            }
        }

        repo = CalendarSyncStateRepository("test-table", table=mock_table)
        result = repo.get_sync_state("user-001", "google")

        assert result is not None
        assert result.user_id == "user-001"
        assert result.provider == "google"
        assert result.subscription_id == "channel-abc123"

    def test_delete_sync_state_removes_route_items(
        self, google_sync_state: CalendarSyncState
    ) -> None:
        """Should delete SYNC item and routing item atomically."""
        from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository

        mock_dynamodb = MagicMock()
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "USER#user-001#PROVIDER#google",
                "sk": "SYNC",
                "user_id": "user-001",
                "provider": "google",
                "provider_calendar_id": "primary",
                "subscription_id": "channel-abc123",
                "created_at": "2025-01-05T12:00:00+00:00",
                "updated_at": "2025-01-05T12:00:00+00:00",
            }
        }

        repo = CalendarSyncStateRepository("test-table", dynamodb=mock_dynamodb, table=mock_table)
        repo.delete_sync_state("user-001", "google")

        # Should delete 2 items: SYNC + GOOGLE#CHANNEL# route
        mock_dynamodb.transact_write_items.assert_called_once()
        call_args = mock_dynamodb.transact_write_items.call_args[1]
        items = call_args["TransactItems"]
        assert len(items) == 2
