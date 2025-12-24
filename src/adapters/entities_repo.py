"""DynamoDB repository for knowledge graph entities."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# Support both Lambda (core.models) and test (src.core.models) import paths
try:
    from core.models import Entity, EntityStatus, EntityType
except ImportError:
    from src.core.models import Entity, EntityStatus, EntityType

logger = logging.getLogger(__name__)


class EntitiesRepository:
    """Repository for managing knowledge graph entities and aliases.

    Handles interaction with two tables:
    1. kairos-entities: specific entity data (PK: USER#<uid>, SK: ENTITY#<eid>)
    2. kairos-entity-aliases: inverted index (PK: USER#<uid>, SK: ALIAS#<alias>)
    """

    def __init__(
        self, entities_table_name: str, aliases_table_name: str, region: str = "eu-west-1"
    ) -> None:
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.entities_table = self.dynamodb.Table(entities_table_name)
        self.aliases_table = self.dynamodb.Table(aliases_table_name)

    def get_by_id(self, user_id: str, entity_id: str) -> Entity | None:
        """Get an entity by its ID."""
        pk = f"USER#{user_id}"
        sk = f"ENTITY#{entity_id}"

        response = self.entities_table.get_item(Key={"pk": pk, "sk": sk})
        item = response.get("Item")

        if not item:
            return None

        return self._item_to_entity(item)

    def get_by_email(self, user_id: str, email: str) -> Entity | None:
        """Get a Person entity by email (deterministic lookup)."""
        # Query GSI2 (email index)
        response = self.entities_table.query(
            IndexName="GSI2",
            KeyConditionExpression=Key("gsi2pk").eq(f"USER#{user_id}")
            & Key("gsi2sk").eq(f"EMAIL#{email.lower()}"),
            Limit=1,
        )

        items = response.get("Items", [])
        if not items:
            return None

        return self._item_to_entity(items[0])

    def get_or_create_by_email(self, user_id: str, email: str, name: str) -> Entity:
        """Get existing entity by email or create a new RESOLVED one.

        Used for meeting attendees where we have a confirmed email.
        """
        existing = self.get_by_email(user_id, email)
        if existing:
            # Update display name if currently just email
            if existing.display_name == email and name != email:
                self.update_display_name(user_id, existing.entity_id, name)
                existing.display_name = name
            return existing

        # Create new resolved entity
        entity = Entity(
            user_id=user_id,
            type=EntityType.PERSON,
            display_name=name,
            primary_email=email.lower(),
            status=EntityStatus.RESOLVED,
            aliases=[name.lower(), email.lower()],
        )

        self.save_entity(entity)
        return entity

    def create_provisional(
        self, user_id: str, mention_text: str, entity_type: EntityType
    ) -> Entity:
        """Create a new PROVISIONAL entity from an unmatched mention."""
        entity = Entity(
            user_id=user_id,
            type=entity_type,
            display_name=mention_text,
            status=EntityStatus.PROVISIONAL,
            aliases=[mention_text.lower()],
        )

        self.save_entity(entity)
        return entity

    def save_entity(self, entity: Entity) -> None:
        """Save an entity and update alias index."""
        # 1. Save entity record
        item = self._entity_to_item(entity)
        self.entities_table.put_item(Item=item)

        # 2. Update alias index for all aliases
        # Note: In a real prod system, this might be async/stream-based or transactional
        # For now, we do direct writes
        for alias in entity.aliases:
            self._save_alias(entity.user_id, alias, entity.entity_id, entity.type)

    def _save_alias(self, user_id: str, alias: str, entity_id: str, type: EntityType) -> None:
        """Save an entry to the inverted alias index."""
        pk = f"USER#{user_id}"
        sk = f"ALIAS#{alias}"  # Alias should be pre-normalized/lowercased

        self.aliases_table.put_item(
            Item={
                "pk": pk,
                "sk": sk,
                "entity_id": entity_id,
                "type": type.value,
                "updated_at": datetime.now(UTC).isoformat(),
                # GSI1 for querying all aliases of an entity (e.g. for merges)
                "gsi1pk": f"ENTITY#{entity_id}",
                "gsi1sk": sk,
            }
        )

    def query_by_alias(self, user_id: str, alias_query: str) -> list[str]:
        """Find candidate entity IDs matching an alias exactly."""
        pk = f"USER#{user_id}"
        sk = f"ALIAS#{alias_query.lower()}"

        response = self.aliases_table.get_item(Key={"pk": pk, "sk": sk})
        item = response.get("Item")

        if item:
            return [item["entity_id"]]
        return []

    def update_display_name(self, user_id: str, entity_id: str, new_name: str) -> None:
        """Update just the display name of an entity."""
        pk = f"USER#{user_id}"
        sk = f"ENTITY#{entity_id}"

        self.entities_table.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET display_name = :n, updated_at = :t",
            ExpressionAttributeValues={":n": new_name, ":t": datetime.now(UTC).isoformat()},
        )

    def _entity_to_item(self, entity: Entity) -> dict[str, Any]:
        """Convert Entity object to DynamoDB item."""
        data: dict[str, Any] = entity.model_dump()

        # Add keys
        data["pk"] = f"USER#{entity.user_id}"
        data["sk"] = f"ENTITY#{entity.entity_id}"

        # GSI1: Type lookup
        data["gsi1pk"] = f"USER#{entity.user_id}"
        data["gsi1sk"] = f"TYPE#{entity.type.value}"

        # GSI2: Email lookup (if present)
        if entity.primary_email:
            data["gsi2pk"] = f"USER#{entity.user_id}"
            data["gsi2sk"] = f"EMAIL#{entity.primary_email}"

        return data

    def _item_to_entity(self, item: dict[str, Any]) -> Entity:
        """Convert DynamoDB item to Entity object."""
        # Clean up DB-specific keys before passing to Pydantic
        clean_item = {
            k: v
            for k, v in item.items()
            if k not in ["pk", "sk", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk"]
        }
        return Entity(**clean_item)
