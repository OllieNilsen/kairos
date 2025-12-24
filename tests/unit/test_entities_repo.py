"""Unit tests for entities repository adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.entities_repo import EntitiesRepository
from src.core.models import Entity, EntityStatus, EntityType


class TestEntitiesRepository:
    """Tests for EntitiesRepository."""

    @pytest.fixture
    def mock_dynamodb(self) -> MagicMock:
        """Create a mock DynamoDB resource."""
        return MagicMock()

    @pytest.fixture
    def mock_entities_table(self) -> MagicMock:
        """Create a mock entities table."""
        return MagicMock()

    @pytest.fixture
    def mock_aliases_table(self) -> MagicMock:
        """Create a mock aliases table."""
        return MagicMock()

    @pytest.fixture
    def repo(
        self,
        mock_dynamodb: MagicMock,
        mock_entities_table: MagicMock,
        mock_aliases_table: MagicMock,
    ) -> EntitiesRepository:
        """Create repository with mocked tables."""
        with patch("boto3.resource") as mock_resource:
            mock_resource.return_value = mock_dynamodb

            def get_table(name: str) -> MagicMock:
                if name == "entities-table":
                    return mock_entities_table
                elif name == "aliases-table":
                    return mock_aliases_table
                return MagicMock()

            mock_dynamodb.Table.side_effect = get_table

            repo = EntitiesRepository("entities-table", "aliases-table")
            # Force set the tables to our specific mocks for verification ease
            repo.entities_table = mock_entities_table
            repo.aliases_table = mock_aliases_table
            return repo

    def test_save_entity_saves_to_table_and_aliases(
        self,
        repo: EntitiesRepository,
        mock_entities_table: MagicMock,
        mock_aliases_table: MagicMock,
    ) -> None:
        """Should save entity to main table and update alias index."""
        entity = Entity(
            user_id="user-001",
            type=EntityType.PERSON,
            display_name="Alice Smith",
            primary_email="alice@example.com",
            aliases=["alice smith", "alice@example.com"],
        )

        repo.save_entity(entity)

        # Verify entity save
        mock_entities_table.put_item.assert_called_once()
        save_call = mock_entities_table.put_item.call_args[1]["Item"]
        assert save_call["pk"] == "USER#user-001"
        assert save_call["sk"] == f"ENTITY#{entity.entity_id}"
        assert save_call["display_name"] == "Alice Smith"
        assert save_call["gsi2sk"] == "EMAIL#alice@example.com"

        # Verify alias saves (2 aliases)
        assert mock_aliases_table.put_item.call_count == 2

        # Check first alias
        alias1_call = mock_aliases_table.put_item.call_args_list[0][1]["Item"]
        assert alias1_call["pk"] == "USER#user-001"
        assert alias1_call["sk"] == "ALIAS#alice smith"
        assert alias1_call["entity_id"] == entity.entity_id

        # Check second alias
        alias2_call = mock_aliases_table.put_item.call_args_list[1][1]["Item"]
        assert alias2_call["sk"] == "ALIAS#alice@example.com"

    def test_get_by_id_found(
        self, repo: EntitiesRepository, mock_entities_table: MagicMock
    ) -> None:
        """Should retrieve entity by ID."""
        entity_id = "ent-123"
        mock_entities_table.get_item.return_value = {
            "Item": {
                "pk": "USER#user-001",
                "sk": f"ENTITY#{entity_id}",
                "entity_id": entity_id,
                "user_id": "user-001",
                "type": "Person",
                "display_name": "Bob",
                "status": "provisional",
                "aliases": ["bob"],
            }
        }

        result = repo.get_by_id("user-001", entity_id)

        assert result is not None
        assert result.entity_id == entity_id
        assert result.display_name == "Bob"
        assert result.type == EntityType.PERSON
        mock_entities_table.get_item.assert_called_once()

    def test_get_by_id_not_found(
        self, repo: EntitiesRepository, mock_entities_table: MagicMock
    ) -> None:
        """Should return None if entity not found."""
        mock_entities_table.get_item.return_value = {}
        result = repo.get_by_id("user-001", "missing")
        assert result is None

    def test_get_by_email_found(
        self, repo: EntitiesRepository, mock_entities_table: MagicMock
    ) -> None:
        """Should retrieve entity by email using GSI2."""
        mock_entities_table.query.return_value = {
            "Items": [
                {
                    "pk": "USER#user-001",
                    "sk": "ENTITY#ent-123",
                    "entity_id": "ent-123",
                    "user_id": "user-001",
                    "type": "Person",
                    "display_name": "Charlie",
                    "primary_email": "charlie@example.com",
                    "status": "resolved",
                }
            ]
        }

        result = repo.get_by_email("user-001", "charlie@example.com")

        assert result is not None
        assert result.primary_email == "charlie@example.com"

        # Verify query structure
        mock_entities_table.query.assert_called_once()
        kwargs = mock_entities_table.query.call_args[1]
        assert kwargs["IndexName"] == "GSI2"

    def test_get_or_create_by_email_existing(
        self, repo: EntitiesRepository, mock_entities_table: MagicMock
    ) -> None:
        """Should return existing entity if email matches."""
        # Mock finding existing entity
        mock_entities_table.query.return_value = {
            "Items": [
                {
                    "pk": "USER#user-001",
                    "sk": "ENTITY#ent-123",
                    "entity_id": "ent-123",
                    "user_id": "user-001",
                    "type": "Person",
                    "display_name": "Dave",
                    "primary_email": "dave@example.com",
                    "status": "resolved",
                    "aliases": ["dave", "dave@example.com"],
                }
            ]
        }

        entity = repo.get_or_create_by_email("user-001", "dave@example.com", "Dave")

        assert entity.entity_id == "ent-123"
        # Should NOT call put_item since it exists and name matches
        mock_entities_table.put_item.assert_not_called()

    def test_get_or_create_by_email_updates_name(
        self, repo: EntitiesRepository, mock_entities_table: MagicMock
    ) -> None:
        """Should update display name if existing is just the email."""
        # Mock existing entity with email as name
        mock_entities_table.query.return_value = {
            "Items": [
                {
                    "pk": "USER#user-001",
                    "sk": "ENTITY#ent-123",
                    "entity_id": "ent-123",
                    "user_id": "user-001",
                    "type": "Person",
                    "display_name": "eve@example.com",  # Old name is email
                    "primary_email": "eve@example.com",
                    "status": "resolved",
                    "aliases": ["eve@example.com"],
                }
            ]
        }

        entity = repo.get_or_create_by_email("user-001", "eve@example.com", "Eve Smith")

        assert entity.display_name == "Eve Smith"
        # Should call update_item
        mock_entities_table.update_item.assert_called_once()

    def test_get_or_create_by_email_creates_new(
        self, repo: EntitiesRepository, mock_entities_table: MagicMock
    ) -> None:
        """Should create new RESOLVED entity if not found."""
        mock_entities_table.query.return_value = {"Items": []}

        entity = repo.get_or_create_by_email("user-001", "frank@example.com", "Frank")

        assert entity.status == EntityStatus.RESOLVED
        assert entity.primary_email == "frank@example.com"
        assert entity.display_name == "Frank"
        assert "frank" in entity.aliases
        assert "frank@example.com" in entity.aliases

        mock_entities_table.put_item.assert_called_once()

    def test_create_provisional(
        self, repo: EntitiesRepository, mock_entities_table: MagicMock
    ) -> None:
        """Should create a PROVISIONAL entity."""
        entity = repo.create_provisional("user-001", "Unknown Person", EntityType.PERSON)

        assert entity.status == EntityStatus.PROVISIONAL
        assert entity.display_name == "Unknown Person"
        assert entity.aliases == ["unknown person"]

        mock_entities_table.put_item.assert_called_once()

    def test_query_by_alias_found(
        self, repo: EntitiesRepository, mock_aliases_table: MagicMock
    ) -> None:
        """Should return entity_id for matching alias."""
        mock_aliases_table.get_item.return_value = {"Item": {"entity_id": "ent-123"}}

        results = repo.query_by_alias("user-001", "Bob Smith")

        assert results == ["ent-123"]
        # Should lowercase the query
        call_args = mock_aliases_table.get_item.call_args[1]
        assert call_args["Key"]["sk"] == "ALIAS#bob smith"

    def test_query_by_alias_not_found(
        self, repo: EntitiesRepository, mock_aliases_table: MagicMock
    ) -> None:
        """Should return empty list for no match."""
        mock_aliases_table.get_item.return_value = {}
        results = repo.query_by_alias("user-001", "Nobody")
        assert results == []
