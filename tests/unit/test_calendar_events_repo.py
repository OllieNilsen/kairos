"""Unit tests for CalendarEventsRepository."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.adapters.calendar_events_repo import (
    CalendarEventsRepository,
    RedirectHopLimitError,
    RedirectLoopError,
)
from src.core.models import KairosCalendarEvent


@pytest.fixture
def sample_event():
    """Create a sample KCNF event for testing."""
    return KairosCalendarEvent(
        user_id="user123",
        provider="google",
        provider_event_id="event123",
        provider_version="etag123",
        title="Team Meeting",
        start=datetime(2025, 1, 5, 10, 0, 0, tzinfo=UTC),
        end=datetime(2025, 1, 5, 11, 0, 0, tzinfo=UTC),
        ingested_at=datetime(2025, 1, 5, 9, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def mock_table():
    """Create a mock DynamoDB table."""
    return MagicMock()


@pytest.fixture
def repo(mock_table):
    """Create repository with mocked DynamoDB."""
    with patch("src.adapters.calendar_events_repo.boto3.resource") as mock_resource:
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_resource.return_value = mock_dynamodb

        repo = CalendarEventsRepository(table_name="test-table")
        repo.table = mock_table
        repo.dynamodb = mock_dynamodb
        return repo


class TestCalendarEventsRepository:
    """Tests for CalendarEventsRepository."""

    def test_compute_main_sk(self, repo, sample_event):
        """Should compute main table sort key correctly."""
        sk = repo._compute_main_sk(sample_event)

        assert sk == "EVT#2025-01-05T10:00:00+00:00#google#event123"

    def test_compute_gsi_day(self, repo, sample_event):
        """Should compute GSI_DAY key in user's local timezone."""
        # Event at 10:00 UTC = 05:00 EST (America/New_York)
        gsi_day = repo._compute_gsi_day(sample_event, user_timezone="America/New_York")

        assert gsi_day == "USER#user123#DAY#2025-01-05"

    def test_compute_gsi_day_crosses_day_boundary(self, repo):
        """Should handle events that cross day boundary in user timezone."""
        # Event at 2025-01-06 03:00 UTC = 2025-01-05 22:00 EST
        event = KairosCalendarEvent(
            user_id="user123",
            provider="google",
            provider_event_id="event123",
            provider_version="etag123",
            start=datetime(2025, 1, 6, 3, 0, 0, tzinfo=UTC),
            end=datetime(2025, 1, 6, 4, 0, 0, tzinfo=UTC),
            ingested_at=datetime.now(UTC),
        )

        gsi_day = repo._compute_gsi_day(event, user_timezone="America/New_York")

        # Should be 2025-01-05 in EST, not 2025-01-06
        assert gsi_day == "USER#user123#DAY#2025-01-05"

    def test_compute_gsi_provider_id(self, repo, sample_event):
        """Should compute GSI_PROVIDER_ID keys correctly."""
        gsi2pk, gsi2sk = repo._compute_gsi_provider_id(sample_event)

        assert gsi2pk == "USER#user123"
        assert gsi2sk == "PROVIDER#google#EVENT#event123"

    def test_save_event(self, repo, mock_table, sample_event):
        """Should save event to DynamoDB."""
        repo.save_event(sample_event, user_timezone="UTC")

        mock_table.put_item.assert_called_once()
        call_args = mock_table.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["pk"] == "USER#user123"
        assert item["sk"].startswith("EVT#2025-01-05T10:00:00")
        assert item["gsi1pk"] == "USER#user123#DAY#2025-01-05"
        assert item["gsi2pk"] == "USER#user123"
        assert item["gsi2sk"] == "PROVIDER#google#EVENT#event123"

    def test_get_event_returns_event(self, repo, mock_table, sample_event):
        """Should retrieve event by PK/SK."""
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "USER#user123",
                "sk": "EVT#2025-01-05T10:00:00+00:00#google#event123",
                "item_type": "event",
                **sample_event.model_dump(mode="json"),
            }
        }

        result = repo.get_event("user123", "EVT#2025-01-05T10:00:00+00:00#google#event123")

        assert result is not None
        assert result.provider_event_id == "event123"
        assert result.item_type == "event"

    def test_get_event_returns_none_if_not_found(self, repo, mock_table):
        """Should return None if event not found."""
        mock_table.get_item.return_value = {}

        result = repo.get_event("user123", "EVT#nonexistent")

        assert result is None

    def test_get_event_follows_redirect(self, repo, mock_table, sample_event):
        """Should follow redirect to new event location."""
        # First call returns redirect
        # Second call returns actual event
        mock_table.get_item.side_effect = [
            {
                "Item": {
                    "pk": "USER#user123",
                    "sk": "EVT#old-time",
                    "item_type": "redirect",
                    "redirect_to_sk": "EVT#new-time",
                }
            },
            {
                "Item": {
                    "pk": "USER#user123",
                    "sk": "EVT#new-time",
                    "item_type": "event",
                    **sample_event.model_dump(mode="json"),
                }
            },
        ]

        result = repo.get_event("user123", "EVT#old-time")

        assert result is not None
        assert result.provider_event_id == "event123"
        assert mock_table.get_item.call_count == 2

    def test_get_event_detects_redirect_loop(self, repo, mock_table):
        """Should detect and raise error on redirect loop."""
        mock_table.get_item.side_effect = [
            {"Item": {"item_type": "redirect", "redirect_to_sk": "EVT#b"}},
            {"Item": {"item_type": "redirect", "redirect_to_sk": "EVT#a"}},
        ]

        with pytest.raises(RedirectLoopError):
            repo.get_event("user123", "EVT#a")

    def test_get_event_hop_limit_exceeded(self, repo, mock_table):
        """Should raise error if hop limit exceeded."""
        # Create a chain of redirects that exceeds hop limit
        # EVT#a -> EVT#b -> EVT#c -> EVT#d (3 hops, exceeds limit of 2)
        mock_table.get_item.side_effect = [
            {"Item": {"item_type": "redirect", "redirect_to_sk": "EVT#b"}},
            {"Item": {"item_type": "redirect", "redirect_to_sk": "EVT#c"}},
            {"Item": {"item_type": "redirect", "redirect_to_sk": "EVT#d"}},
        ]

        with pytest.raises(RedirectHopLimitError):
            repo.get_event("user123", "EVT#a", max_redirect_hops=2)

    def test_get_by_provider_event_id_returns_event(self, repo, mock_table, sample_event):
        """Should query GSI_PROVIDER_ID and return event."""
        mock_table.query.return_value = {
            "Items": [
                {
                    "pk": "USER#user123",
                    "sk": "EVT#2025-01-05T10:00:00+00:00#google#event123",
                    "item_type": "event",
                    **sample_event.model_dump(mode="json"),
                }
            ]
        }

        result = repo.get_by_provider_event_id("user123", "google", "event123")

        assert result is not None
        assert result.provider_event_id == "event123"
        mock_table.query.assert_called_once()
        call_kwargs = mock_table.query.call_args.kwargs
        assert call_kwargs["IndexName"] == "GSI_PROVIDER_ID"

    def test_get_by_provider_event_id_prefers_event_over_redirect(
        self, repo, mock_table, sample_event
    ):
        """Should prefer event items over redirects when both exist."""
        mock_table.query.return_value = {
            "Items": [
                {"item_type": "redirect", "redirect_to_sk": "EVT#new"},
                {
                    "item_type": "event",
                    **sample_event.model_dump(mode="json"),
                },
            ]
        }

        result = repo.get_by_provider_event_id("user123", "google", "event123")

        assert result is not None
        assert result.item_type == "event"

    def test_get_by_provider_event_id_handles_multiple_events(self, repo, mock_table, sample_event):
        """Should pick newest event if multiple exist (data corruption)."""
        event1 = sample_event.model_dump(mode="json")
        event1["ingested_at"] = "2025-01-05T08:00:00Z"
        event1["item_type"] = "event"

        event2 = sample_event.model_dump(mode="json")
        event2["ingested_at"] = "2025-01-05T09:00:00Z"  # Newer
        event2["item_type"] = "event"

        mock_table.query.return_value = {"Items": [event1, event2]}

        result = repo.get_by_provider_event_id("user123", "google", "event123")

        assert result is not None
        # Should pick event2 (newer ingested_at)

    def test_get_by_provider_event_id_follows_redirect_if_only_option(
        self, repo, mock_table, sample_event
    ):
        """Should follow redirect if no event items exist."""
        mock_table.query.return_value = {
            "Items": [{"item_type": "redirect", "redirect_to_sk": "EVT#new-time"}]
        }

        mock_table.get_item.return_value = {
            "Item": {
                "item_type": "event",
                **sample_event.model_dump(mode="json"),
            }
        }

        result = repo.get_by_provider_event_id("user123", "google", "event123")

        assert result is not None
        mock_table.get_item.assert_called_once()

    def test_get_by_provider_event_id_returns_none_if_not_found(self, repo, mock_table):
        """Should return None if provider event not found."""
        mock_table.query.return_value = {"Items": []}

        result = repo.get_by_provider_event_id("user123", "google", "nonexistent")

        assert result is None

    def test_list_events_by_day(self, repo, mock_table, sample_event):
        """Should query GSI_DAY and return only event items."""
        event1 = sample_event.model_dump(mode="json")
        event1["item_type"] = "event"
        event1["title"] = "Event 1"

        event2 = sample_event.model_dump(mode="json")
        event2["item_type"] = "event"
        event2["title"] = "Event 2"

        mock_table.query.return_value = {"Items": [event1, event2]}

        results = repo.list_events_by_day("user123", "2025-01-05", "UTC")

        assert len(results) == 2
        assert results[0].title == "Event 1"
        assert results[1].title == "Event 2"
        mock_table.query.assert_called_once()
        call_kwargs = mock_table.query.call_args.kwargs
        assert call_kwargs["IndexName"] == "GSI_DAY"

    def test_list_events_by_day_filters_redirects(self, repo, mock_table, sample_event):
        """Should filter out redirect/tombstone items."""
        event1 = sample_event.model_dump(mode="json")
        event1["item_type"] = "event"

        redirect = {"item_type": "redirect", "redirect_to_sk": "EVT#new"}

        mock_table.query.return_value = {"Items": [event1, redirect]}

        results = repo.list_events_by_day("user123", "2025-01-05", "UTC")

        # Should only return the event, not the redirect
        assert len(results) == 1
        assert results[0].item_type == "event"

    def test_update_event_start_time_transaction_success(self, repo, sample_event):
        """Should execute Put+Update transaction for start_time changes."""
        old_event = sample_event
        new_event = sample_event.model_copy()
        new_event.start = datetime(2025, 1, 5, 14, 0, 0, tzinfo=UTC)

        mock_client = MagicMock()
        repo.dynamodb.meta.client = mock_client
        mock_client.transact_write_items.return_value = {}

        repo.update_event_start_time(old_event, new_event, user_timezone="UTC")

        mock_client.transact_write_items.assert_called_once()
        call_args = mock_client.transact_write_items.call_args
        transact_items = call_args.kwargs["TransactItems"]

        # Should have 2 items: Put new + Update old
        assert len(transact_items) == 2
        assert "Put" in transact_items[0]
        assert "Update" in transact_items[1]

    def test_update_event_start_time_version_guard(self, repo, sample_event):
        """Should include provider_version in condition expression."""
        old_event = sample_event
        new_event = sample_event.model_copy()
        new_event.start = datetime(2025, 1, 5, 14, 0, 0, tzinfo=UTC)

        mock_client = MagicMock()
        repo.dynamodb.meta.client = mock_client
        mock_client.transact_write_items.return_value = {}

        repo.update_event_start_time(old_event, new_event, user_timezone="UTC")

        call_args = mock_client.transact_write_items.call_args
        update_item = call_args.kwargs["TransactItems"][1]["Update"]

        # Should verify provider_version in condition
        assert "provider_version = :provider_version" in update_item["ConditionExpression"]

    def test_update_event_start_time_creates_redirect_tombstone(self, repo, sample_event):
        """Should update old item to redirect tombstone."""
        old_event = sample_event
        new_event = sample_event.model_copy()
        new_event.start = datetime(2025, 1, 5, 14, 0, 0, tzinfo=UTC)

        mock_client = MagicMock()
        repo.dynamodb.meta.client = mock_client
        mock_client.transact_write_items.return_value = {}

        repo.update_event_start_time(old_event, new_event, user_timezone="UTC")

        call_args = mock_client.transact_write_items.call_args
        update_item = call_args.kwargs["TransactItems"][1]["Update"]

        # Should set item_type to redirect and add redirect_to_sk
        assert "item_type = :redirect_type" in update_item["UpdateExpression"]
        assert "redirect_to_sk = :new_sk" in update_item["UpdateExpression"]

    def test_update_event_start_time_transaction_failure(self, repo, sample_event):
        """Should raise ClientError if transaction fails."""
        old_event = sample_event
        new_event = sample_event.model_copy()
        new_event.start = datetime(2025, 1, 5, 14, 0, 0, tzinfo=UTC)

        mock_client = MagicMock()
        repo.dynamodb.meta.client = mock_client
        mock_client.transact_write_items.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "TransactWriteItems"
        )

        with pytest.raises(ClientError):
            repo.update_event_start_time(old_event, new_event, user_timezone="UTC")
