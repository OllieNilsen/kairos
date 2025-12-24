"""DynamoDB repository for knowledge graph edges."""

from __future__ import annotations

import logging
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# Support both Lambda (core.models) and test (src.core.models) import paths
try:
    from core.models import Edge, EdgeType
except ImportError:
    from src.core.models import Edge, EdgeType

logger = logging.getLogger(__name__)


class EdgesRepository:
    """Repository for managing knowledge graph edges (relationships).

    Uses dual-write pattern in 'kairos-edges' table:
    1. EDGEOUT: pk=USER#<uid>#OUT#<from_id>, sk=TYPE#<type>#IN#<to_id>
       - Queries: "What edges go OUT from this entity?"
    2. EDGEIN: pk=USER#<uid>#IN#<to_id>, sk=TYPE#<type>#OUT#<from_id>
       - Queries: "What edges come IN to this entity?"
    """

    def __init__(self, table_name: str, region: str = "eu-west-1") -> None:
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    def create_edge(self, edge: Edge) -> None:
        """Create a new edge (dual-write)."""
        # Save both directions in a transaction for consistency
        try:
            self.dynamodb.meta.client.transact_write_items(
                TransactItems=[
                    {"Put": {"TableName": self.table.name, "Item": self._edge_to_item_out(edge)}},
                    {"Put": {"TableName": self.table.name, "Item": self._edge_to_item_in(edge)}},
                ]
            )
        except Exception as e:
            logger.error(f"Failed to create edge: {e}")
            raise

    def get_edges_from(
        self, user_id: str, entity_id: str, edge_type: EdgeType | None = None
    ) -> list[Edge]:
        """Get all edges outgoing FROM a specific entity."""
        pk = f"USER#{user_id}#OUT#{entity_id}"

        # Query specific edge type or all outgoing edges
        sk_prefix = f"TYPE#{edge_type.value}#" if edge_type else "TYPE#"

        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix)
        )

        return [self._item_to_edge(item) for item in response.get("Items", [])]

    def get_edges_to(
        self, user_id: str, entity_id: str, edge_type: EdgeType | None = None
    ) -> list[Edge]:
        """Get all edges incoming TO a specific entity."""
        pk = f"USER#{user_id}#IN#{entity_id}"

        sk_prefix = f"TYPE#{edge_type.value}#" if edge_type else "TYPE#"

        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix)
        )

        return [self._item_to_edge(item) for item in response.get("Items", [])]

    def _edge_to_item_out(self, edge: Edge) -> dict[str, Any]:
        """Convert Edge to EDGEOUT item."""
        data: dict[str, Any] = edge.model_dump()
        data["pk"] = f"USER#{edge.user_id}#OUT#{edge.from_entity_id}"
        data["sk"] = f"TYPE#{edge.edge_type.value}#IN#{edge.to_entity_id}"
        data["direction"] = "OUT"
        return data

    def _edge_to_item_in(self, edge: Edge) -> dict[str, Any]:
        """Convert Edge to EDGEIN item."""
        data: dict[str, Any] = edge.model_dump()
        data["pk"] = f"USER#{edge.user_id}#IN#{edge.to_entity_id}"
        data["sk"] = f"TYPE#{edge.edge_type.value}#OUT#{edge.from_entity_id}"
        data["direction"] = "IN"
        return data

    def _item_to_edge(self, item: dict[str, Any]) -> Edge:
        """Convert DynamoDB item to Edge object."""
        # Clean up DB-specific keys
        clean_item = {k: v for k, v in item.items() if k not in ["pk", "sk", "direction"]}
        return Edge(**clean_item)
