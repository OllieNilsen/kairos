"""Unit tests for DynamoDB deduplicator."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.adapters.dynamodb import CallDeduplicator


class TestCallDeduplicator:
    """Tests for CallDeduplicator."""

    def test_new_call_returns_false(self):
        """New call_id should return False (not a duplicate)."""
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}  # Successful put

        with patch("src.adapters.dynamodb.boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_table

            deduplicator = CallDeduplicator("test-table")
            result = deduplicator.is_duplicate("new-call-123")

        assert result is False
        mock_table.put_item.assert_called_once()

    def test_duplicate_call_returns_true(self):
        """Existing call_id should return True (is a duplicate)."""
        mock_table = MagicMock()
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "PutItem",
        )

        with patch("src.adapters.dynamodb.boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_table

            deduplicator = CallDeduplicator("test-table")
            result = deduplicator.is_duplicate("existing-call-456")

        assert result is True

    def test_other_error_raises(self):
        """Other DynamoDB errors should be re-raised."""
        mock_table = MagicMock()
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}},
            "PutItem",
        )

        with patch("src.adapters.dynamodb.boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_table

            deduplicator = CallDeduplicator("test-table")

            with pytest.raises(ClientError):
                deduplicator.is_duplicate("some-call-789")

    def test_put_item_includes_ttl(self):
        """Put item should include TTL for auto-cleanup."""
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}

        with patch("src.adapters.dynamodb.boto3.resource") as mock_resource:
            mock_resource.return_value.Table.return_value = mock_table

            deduplicator = CallDeduplicator("test-table")
            deduplicator.is_duplicate("call-with-ttl")

        call_args = mock_table.put_item.call_args
        item = call_args.kwargs["Item"]

        assert "call_id" in item
        assert "processed_at" in item
        assert "ttl" in item
        assert isinstance(item["ttl"], int)
