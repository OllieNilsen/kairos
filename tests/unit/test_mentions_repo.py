"""Unit tests for mentions repository adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.mentions_repo import MentionsRepository
from src.core.models import (
    CandidateScore,
    EntityType,
    Mention,
    MentionEvidence,
    ResolutionState,
)


class TestMentionsRepository:
    """Tests for MentionsRepository."""

    @pytest.fixture
    def mock_dynamodb(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def repo(self, mock_dynamodb: MagicMock) -> MentionsRepository:
        with patch("boto3.resource") as mock_resource:
            mock_resource.return_value = mock_dynamodb
            mock_table = MagicMock()
            mock_dynamodb.Table.return_value = mock_table
            return MentionsRepository("mentions-table")

    @pytest.fixture
    def sample_mention(self) -> Mention:
        """Create a sample mention."""
        return Mention(
            user_id="user-001",
            mention_text="Bob",
            type=EntityType.PERSON,
            local_context="Bob said hello.",
            evidence=MentionEvidence(
                meeting_id="m-1", segment_id="s-1", t0=10.0, t1=12.0, quote="Bob said hello."
            ),
        )

    def test_create_mention_stores_gsi_keys(
        self, repo: MentionsRepository, sample_mention: Mention
    ) -> None:
        """Should save mention with correct GSI keys for state."""
        repo.create_mention(sample_mention)

        repo.table.put_item.assert_called_once()
        item = repo.table.put_item.call_args[1]["Item"]

        assert item["pk"] == "USER#user-001"
        assert item["sk"] == f"MENTION#{sample_mention.mention_id}"
        # Only ambiguous mentions are unlinked initially
        assert item["gsi1sk"] == "UNLINKED"
        # State index
        assert item["gsi2pk"] == "USER#user-001"
        assert item["gsi2sk"] == f"STATE#{ResolutionState.AMBIGUOUS.value}"

    def test_get_mention_found(self, repo: MentionsRepository, sample_mention: Mention) -> None:
        """Should retrieve mention by ID."""
        item = sample_mention.model_dump()
        item["pk"] = "USER#user-001"
        item["sk"] = f"MENTION#{sample_mention.mention_id}"

        repo.table.get_item.return_value = {"Item": item}

        result = repo.get_mention("user-001", sample_mention.mention_id)

        assert result is not None
        assert result.mention_id == sample_mention.mention_id
        assert result.mention_text == "Bob"

    def test_get_mention_not_found(self, repo: MentionsRepository) -> None:
        """Should return None if not found."""
        repo.table.get_item.return_value = {}
        result = repo.get_mention("user-001", "missing")
        assert result is None

    def test_get_ambiguous_mentions(self, repo: MentionsRepository) -> None:
        """Should query GSI2 for ambiguous mentions."""
        repo.table.query.return_value = {
            "Items": [
                {
                    "pk": "USER#user-001",
                    "sk": "MENTION#m-123",
                    "user_id": "user-001",
                    "mention_id": "m-123",
                    "mention_text": "Alice",
                    "type": "Person",
                    "local_context": "Context",
                    "resolution_state": "ambiguous",
                    "evidence": {
                        "meeting_id": "m-1",
                        "segment_id": "s-1",
                        "t0": 0,
                        "t1": 1,
                        "quote": "",
                    },
                }
            ]
        }

        results = repo.get_ambiguous_mentions("user-001")

        assert len(results) == 1
        assert results[0].resolution_state == ResolutionState.AMBIGUOUS

        # Verify query
        repo.table.query.assert_called_once()
        kwargs = repo.table.query.call_args[1]
        assert kwargs["IndexName"] == "GSI2"

    def test_mark_linked(self, repo: MentionsRepository) -> None:
        """Should update resolution state and set GSI1 (Entity) key."""
        repo.mark_linked("user-001", "m-123", "ent-456", 0.95)

        repo.table.update_item.assert_called_once()
        kwargs = repo.table.update_item.call_args[1]

        # Check updates
        expr_vals = kwargs["ExpressionAttributeValues"]
        assert expr_vals[":s"] == "linked"
        assert expr_vals[":e"] == "ent-456"
        assert expr_vals[":gs"] == "ENTITY#ent-456"  # GSI1 updated
        assert expr_vals[":g2s"] == "STATE#linked"  # GSI2 updated

    def test_mark_ambiguous(self, repo: MentionsRepository) -> None:
        """Should update state and store candidates."""
        candidates = ["ent-1", "ent-2"]
        scores = [
            CandidateScore(entity_id="ent-1", score=0.8, confidence="MEDIUM", reasoning="Maybe")
        ]

        repo.mark_ambiguous("user-001", "m-123", candidates, scores)

        repo.table.update_item.assert_called_once()
        kwargs = repo.table.update_item.call_args[1]

        expr_vals = kwargs["ExpressionAttributeValues"]
        assert expr_vals[":s"] == "ambiguous"
        assert expr_vals[":c"] == candidates
        assert expr_vals[":cs"][0]["entity_id"] == "ent-1"
