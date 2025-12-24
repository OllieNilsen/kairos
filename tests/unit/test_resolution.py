"""Unit tests for entity resolution service."""

from __future__ import annotations

from unittest.mock import ANY, MagicMock

import pytest

from src.core.extraction import VerificationResult
from src.core.models import (
    Entity,
    EntityType,
    MentionExtraction,
    TranscriptSegment,
)
from src.core.resolution import EntityResolutionService


class TestEntityResolutionService:
    @pytest.fixture
    def mock_extractor(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_entities_repo(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_mentions_repo(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_transcripts_repo(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def service(
        self,
        mock_extractor: MagicMock,
        mock_entities_repo: MagicMock,
        mock_mentions_repo: MagicMock,
        mock_transcripts_repo: MagicMock,
    ) -> EntityResolutionService:
        return EntityResolutionService(
            mock_extractor,
            mock_entities_repo,
            mock_mentions_repo,
            mock_transcripts_repo,
        )

    @pytest.fixture
    def sample_segment(self) -> TranscriptSegment:
        return TranscriptSegment(segment_id="s1", text="Hello Bob", t0=0.0, t1=1.0, speaker="Me")

    def test_process_meeting_flow(
        self,
        service: EntityResolutionService,
        mock_transcripts_repo: MagicMock,
        mock_extractor: MagicMock,
        mock_entities_repo: MagicMock,
        mock_mentions_repo: MagicMock,
        sample_segment: TranscriptSegment,
    ) -> None:
        """Should fetch transcript, extract, and resolve mentions."""
        # 1. Mock transcript
        mock_transcripts_repo.get_transcript.return_value = [sample_segment]

        # 2. Mock extraction
        extraction = MentionExtraction(
            mention_text="Bob", type=EntityType.PERSON, segment_id="s1", quote="Hello Bob"
        )
        mock_extractor.extract_mentions.return_value = [
            VerificationResult(is_valid=True, cleaned_extraction=extraction)
        ]

        # 3. Mock entity resolution (provisional creation)
        mock_entities_repo.query_by_alias.return_value = []
        mock_entities_repo.create_provisional.return_value = Entity(
            user_id="u1", type=EntityType.PERSON, display_name="Bob", entity_id="e1"
        )

        service.process_meeting("u1", "m1")

        # Verify flow
        mock_transcripts_repo.get_transcript.assert_called_with("u1", "m1")
        mock_extractor.extract_mentions.assert_called_once()

        # Verify resolution
        mock_mentions_repo.create_mention.assert_called_once()
        mock_entities_repo.create_provisional.assert_called_once()
        mock_mentions_repo.mark_linked.assert_called_with("u1", ANY, "e1", confidence=1.0)

    def test_process_meeting_no_transcript(
        self,
        service: EntityResolutionService,
        mock_transcripts_repo: MagicMock,
        mock_extractor: MagicMock,
    ) -> None:
        """Should do nothing if no transcript found."""
        mock_transcripts_repo.get_transcript.return_value = []

        service.process_meeting("u1", "m1")

        mock_extractor.extract_mentions.assert_not_called()

    def test_process_meeting_skips_invalid_extractions(
        self,
        service: EntityResolutionService,
        mock_transcripts_repo: MagicMock,
        mock_extractor: MagicMock,
        mock_mentions_repo: MagicMock,
        sample_segment: TranscriptSegment,
    ) -> None:
        """Should ignore invalid extractions."""
        mock_transcripts_repo.get_transcript.return_value = [sample_segment]
        mock_extractor.extract_mentions.return_value = [
            VerificationResult(is_valid=False, errors=["error"])
        ]

        service.process_meeting("u1", "m1")

        mock_mentions_repo.create_mention.assert_not_called()

    def test_resolve_mention_existing_alias(
        self,
        service: EntityResolutionService,
        mock_entities_repo: MagicMock,
        mock_mentions_repo: MagicMock,
        sample_segment: TranscriptSegment,
    ) -> None:
        """Should link to existing entity if alias match found."""
        extraction = MentionExtraction(
            mention_text="Alice", type=EntityType.PERSON, segment_id="s1", quote="Alice is here"
        )

        # Mock alias find
        mock_entities_repo.query_by_alias.return_value = ["ent-alice-123"]

        service.resolve_mention("u1", "m1", extraction, sample_segment)

        # Should create mention
        mock_mentions_repo.create_mention.assert_called_once()

        # Should NOT create new entity
        mock_entities_repo.create_provisional.assert_not_called()

        # Should link to EXISTING id
        mock_mentions_repo.mark_linked.assert_called_with(
            "u1", ANY, "ent-alice-123", confidence=1.0
        )

    def test_resolve_mention_creates_provisional(
        self,
        service: EntityResolutionService,
        mock_entities_repo: MagicMock,
        mock_mentions_repo: MagicMock,
        sample_segment: TranscriptSegment,
    ) -> None:
        """Should create provisional entity if no alias match."""
        extraction = MentionExtraction(
            mention_text="Unknown User",
            type=EntityType.PERSON,
            segment_id="s1",
            quote="Who is Unknown User?",
        )

        # No alias match
        mock_entities_repo.query_by_alias.return_value = []

        # Mock creation
        new_entity = Entity(
            user_id="u1", type=EntityType.PERSON, display_name="Unknown User", entity_id="ent-new"
        )
        mock_entities_repo.create_provisional.return_value = new_entity

        service.resolve_mention("u1", "m1", extraction, sample_segment)

        # Should create mention
        mock_mentions_repo.create_mention.assert_called_once()

        # Should create provisional entity
        mock_entities_repo.create_provisional.assert_called_with(
            "u1", "Unknown User", EntityType.PERSON
        )

        # Should link to NEW id
        mock_mentions_repo.mark_linked.assert_called_with("u1", ANY, "ent-new", confidence=1.0)
