"""DynamoDB repository for knowledge graph mentions."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# Support both Lambda (core.models) and test (src.core.models) import paths
try:
    from core.models import CandidateScore, Mention, ResolutionState
except ImportError:
    from src.core.models import CandidateScore, Mention, ResolutionState

logger = logging.getLogger(__name__)


class MentionsRepository:
    """Repository for managing transcript mentions.

    Handles interaction with kairos-mentions table:
    - PK: USER#<uid>, SK: MENTION#<mid>
    - GSI1: Query by Linked Entity (ENTITY#<eid>)
    - GSI2: Query by Resolution State (STATE#<state>)
    """

    def __init__(self, table_name: str, region: str = "eu-west-1") -> None:
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    def create_mention(self, mention: Mention) -> None:
        """Create a new mention."""
        item = self._mention_to_item(mention)
        self.table.put_item(Item=item)

    def get_mention(self, user_id: str, mention_id: str) -> Mention | None:
        """Get a mention by ID."""
        pk = f"USER#{user_id}"
        sk = f"MENTION#{mention_id}"

        response = self.table.get_item(Key={"pk": pk, "sk": sk})
        item = response.get("Item")

        if not item:
            return None

        return self._item_to_mention(item)

    def get_ambiguous_mentions(self, user_id: str) -> list[Mention]:
        """Get all mentions that need resolution (Ambiguous or New)."""
        # Query GSI2 for AMBIGUOUS state
        response = self.table.query(
            IndexName="GSI2",
            KeyConditionExpression=Key("gsi2pk").eq(f"USER#{user_id}")
            & Key("gsi2sk").eq(f"STATE#{ResolutionState.AMBIGUOUS.value}"),
        )

        return [self._item_to_mention(item) for item in response.get("Items", [])]

    def mark_linked(self, user_id: str, mention_id: str, entity_id: str, confidence: float) -> None:
        """Mark a mention as successfully LINKED to an entity."""
        pk = f"USER#{user_id}"
        sk = f"MENTION#{mention_id}"

        self.table.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET resolution_state = :s, linked_entity_id = :e, confidence = :c, gsi1pk = :gp, gsi1sk = :gs, gsi2sk = :g2s, updated_at = :t",
            ExpressionAttributeValues={
                ":s": ResolutionState.LINKED.value,
                ":e": entity_id,
                ":c": confidence,
                ":gp": f"USER#{user_id}",
                ":gs": f"ENTITY#{entity_id}",  # GSI1: Entity lookup
                ":g2s": f"STATE#{ResolutionState.LINKED.value}",  # GSI2: State lookup
                ":t": datetime.now(UTC).isoformat(),
            },
        )

    def mark_ambiguous(
        self, user_id: str, mention_id: str, candidates: list[str], scores: list[CandidateScore]
    ) -> None:
        """Mark a mention as AMBIGUOUS with candidate suggestions."""
        pk = f"USER#{user_id}"
        sk = f"MENTION#{mention_id}"

        # Convert scores to dicts for DynamoDB
        scores_data = [s.model_dump() for s in scores]

        self.table.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET resolution_state = :s, candidate_entity_ids = :c, candidate_scores = :cs, gsi2sk = :g2s, updated_at = :t",
            ExpressionAttributeValues={
                ":s": ResolutionState.AMBIGUOUS.value,
                ":c": candidates,
                ":cs": scores_data,
                ":g2s": f"STATE#{ResolutionState.AMBIGUOUS.value}",
                ":t": datetime.now(UTC).isoformat(),
            },
        )

    def _mention_to_item(self, mention: Mention) -> dict[str, Any]:
        """Convert Mention object to DynamoDB item."""
        data: dict[str, Any] = mention.model_dump()

        # Primary Key
        data["pk"] = f"USER#{mention.user_id}"
        data["sk"] = f"MENTION#{mention.mention_id}"

        # GSI1: Entity Lookup (if linked)
        data["gsi1pk"] = f"USER#{mention.user_id}"
        if mention.linked_entity_id:
            data["gsi1sk"] = f"ENTITY#{mention.linked_entity_id}"
        else:
            data["gsi1sk"] = "UNLINKED"

        # GSI2: Resolution State Lookup
        data["gsi2pk"] = f"USER#{mention.user_id}"
        data["gsi2sk"] = f"STATE#{mention.resolution_state.value}"

        return data

    def _item_to_mention(self, item: dict[str, Any]) -> Mention:
        """Convert DynamoDB item to Mention object."""
        clean_item = {
            k: v
            for k, v in item.items()
            if k not in ["pk", "sk", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk"]
        }
        return Mention(**clean_item)
