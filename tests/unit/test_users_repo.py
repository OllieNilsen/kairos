"""Unit tests for UsersRepository (Slice 4B - Multi-user primitives)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.core.models import User


class TestUsersRepository:
    """Tests for UsersRepository routing and isolation."""

    @pytest.fixture
    def sample_user(self) -> User:
        """Create a sample user."""
        return User(
            user_id="user-001",
            primary_email="alice@example.com",
            phone_number_e164="+442012341234",
            timezone="Europe/London",
            preferred_prompt_time="17:30",
            status="active",
            default_calendar_provider="google",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def test_create_user_success(self, sample_user: User) -> None:
        """Should create user with profile and routing items atomically."""
        from src.adapters.users_repo import UsersRepository

        mock_dynamodb = MagicMock()
        repo = UsersRepository("test-table", dynamodb=mock_dynamodb)

        repo.create_user(sample_user)

        # Should write 3 items transactionally: PROFILE, PHONE#, EMAIL#
        mock_dynamodb.transact_write_items.assert_called_once()
        call_args = mock_dynamodb.transact_write_items.call_args[1]
        items = call_args["TransactItems"]
        assert len(items) == 3  # Profile + phone route + email route

        # Verify profile item
        profile_item = items[0]["Put"]
        assert profile_item["Item"]["pk"]["S"] == "USER#user-001"
        assert profile_item["Item"]["sk"]["S"] == "PROFILE"

        # Verify phone routing item
        phone_item = items[1]["Put"]
        assert phone_item["Item"]["pk"]["S"] == "PHONE#+442012341234"
        assert phone_item["Item"]["sk"]["S"] == "ROUTE"
        assert phone_item["Item"]["user_id"]["S"] == "user-001"

        # Verify email routing item
        email_item = items[2]["Put"]
        assert email_item["Item"]["pk"]["S"] == "EMAIL#alice@example.com"
        assert email_item["Item"]["sk"]["S"] == "ROUTE"
        assert email_item["Item"]["user_id"]["S"] == "user-001"

    def test_create_user_phone_already_exists(self, sample_user: User) -> None:
        """Should raise error if phone number already registered."""
        from src.adapters.users_repo import PhoneAlreadyRegisteredError, UsersRepository

        mock_dynamodb = MagicMock()
        mock_dynamodb.transact_write_items.side_effect = ClientError(
            {"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems"
        )

        repo = UsersRepository("test-table", dynamodb=mock_dynamodb)

        with pytest.raises(PhoneAlreadyRegisteredError, match="already registered"):
            repo.create_user(sample_user)

    def test_get_user_by_phone_success(self, sample_user: User) -> None:
        """Should lookup user_id by phone number (O(1) GetItem)."""
        from src.adapters.users_repo import UsersRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "PHONE#+442012341234",
                "sk": "ROUTE",
                "user_id": "user-001",
                "status": "active",
            }
        }

        repo = UsersRepository("test-table", table=mock_table)
        result = repo.get_user_by_phone("+442012341234")

        assert result == "user-001"
        mock_table.get_item.assert_called_once_with(
            Key={"pk": "PHONE#+442012341234", "sk": "ROUTE"}
        )

    def test_get_user_by_phone_not_found(self) -> None:
        """Should return None if phone not registered."""
        from src.adapters.users_repo import UsersRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {}  # No Item

        repo = UsersRepository("test-table", table=mock_table)
        result = repo.get_user_by_phone("+442099999999")

        assert result is None

    def test_get_user_by_email_success(self) -> None:
        """Should lookup user_id by email (O(1) GetItem)."""
        from src.adapters.users_repo import UsersRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "EMAIL#alice@example.com",
                "sk": "ROUTE",
                "user_id": "user-001",
            }
        }

        repo = UsersRepository("test-table", table=mock_table)
        result = repo.get_user_by_email("alice@example.com")

        assert result == "user-001"
        mock_table.get_item.assert_called_once_with(
            Key={"pk": "EMAIL#alice@example.com", "sk": "ROUTE"}
        )

    def test_get_user_profile_success(self, sample_user: User) -> None:
        """Should fetch full user profile."""
        from src.adapters.users_repo import UsersRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "USER#user-001",
                "sk": "PROFILE",
                "user_id": "user-001",
                "primary_email": "alice@example.com",
                "phone_number_e164": "+442012341234",
                "timezone": "Europe/London",
                "preferred_prompt_time": "17:30",
                "status": "active",
                "default_calendar_provider": "google",
                "created_at": sample_user.created_at.isoformat(),
                "updated_at": sample_user.updated_at.isoformat(),
            }
        }

        repo = UsersRepository("test-table", table=mock_table)
        result = repo.get_user_profile("user-001")

        assert result is not None
        assert result.user_id == "user-001"
        assert result.primary_email == "alice@example.com"
        assert result.phone_number_e164 == "+442012341234"

    def test_get_user_profile_not_found(self) -> None:
        """Should return None if user profile does not exist."""
        from src.adapters.users_repo import UsersRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        repo = UsersRepository("test-table", table=mock_table)
        result = repo.get_user_profile("user-999")

        assert result is None

    def test_phone_enumeration_rate_limit(self) -> None:
        """Should enforce rate limiting on phone lookups (P0 security)."""
        from src.adapters.users_repo import PhoneEnumerationRateLimitError, UsersRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {}  # Not found

        repo = UsersRepository("test-table", table=mock_table)

        # Simulate 11 lookups within rate limit window (max is 10/hour)
        for i in range(11):
            phone = f"+4420123456{i:02d}"
            if i < 10:
                # First 10 should succeed (even if not found)
                result = repo.get_user_by_phone(phone, enforce_rate_limit=True)
                assert result is None
            else:
                # 11th should raise rate limit error
                with pytest.raises(
                    PhoneEnumerationRateLimitError, match="Phone enumeration rate limit"
                ):
                    repo.get_user_by_phone(phone, enforce_rate_limit=True)

    def test_update_user_status(self) -> None:
        """Should update user status (active/paused/stopped)."""
        from src.adapters.users_repo import UsersRepository

        mock_table = MagicMock()
        repo = UsersRepository("test-table", table=mock_table)

        repo.update_user_status("user-001", "stopped")

        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args[1]
        assert call_args["Key"] == {"pk": "USER#user-001", "sk": "PROFILE"}
        assert ":status" in call_args["ExpressionAttributeValues"]
        assert call_args["ExpressionAttributeValues"][":status"] == "stopped"

    def test_delete_user_removes_all_items(self) -> None:
        """Should delete profile and routing items atomically."""
        from src.adapters.users_repo import UsersRepository

        mock_dynamodb = MagicMock()
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "USER#user-001",
                "sk": "PROFILE",
                "user_id": "user-001",
                "phone_number_e164": "+442012341234",
                "primary_email": "alice@example.com",
                "timezone": "UTC",
                "preferred_prompt_time": "17:30",
                "status": "active",
                "created_at": "2025-01-05T12:00:00+00:00",
                "updated_at": "2025-01-05T12:00:00+00:00",
            }
        }

        repo = UsersRepository("test-table", dynamodb=mock_dynamodb, table=mock_table)
        repo.delete_user("user-001")

        # Should delete 3 items: PROFILE, PHONE#, EMAIL#
        mock_dynamodb.transact_write_items.assert_called_once()
        call_args = mock_dynamodb.transact_write_items.call_args[1]
        items = call_args["TransactItems"]
        assert len(items) == 3

    def test_user_isolation(self) -> None:
        """Should enforce user_id isolation (no cross-tenant access)."""
        from src.adapters.users_repo import UsersRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {}  # Not found
        repo = UsersRepository("test-table", table=mock_table)

        # All operations should include USER#<user_id> partition key
        repo.get_user_profile("user-001")
        call_args = mock_table.get_item.call_args[1]
        assert call_args["Key"]["pk"] == "USER#user-001"

    def test_email_normalization(self) -> None:
        """Should normalize email addresses (lowercase) for routing."""
        from src.adapters.users_repo import UsersRepository

        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "EMAIL#alice@example.com",
                "sk": "ROUTE",
                "user_id": "user-001",
            }
        }

        repo = UsersRepository("test-table", table=mock_table)

        # Should normalize to lowercase
        result = repo.get_user_by_email("Alice@Example.com")
        assert result == "user-001"

        # Verify normalized key was used
        mock_table.get_item.assert_called_once_with(
            Key={"pk": "EMAIL#alice@example.com", "sk": "ROUTE"}
        )
