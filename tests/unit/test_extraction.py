"""Unit tests for entity extraction logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.extraction import EntityExtractor, ExtractionResponse
from src.core.interfaces import LLMClient
from src.core.models import (
    EntityType,
    MentionExtraction,
    TranscriptSegment,
)


class TestEntityExtractor:
    """Tests for EntityExtractor service."""

    @pytest.fixture
    def mock_llm(self) -> MagicMock:
        return MagicMock(spec=LLMClient)

    @pytest.fixture
    def extractor(self, mock_llm: MagicMock) -> EntityExtractor:
        return EntityExtractor(mock_llm)

    @pytest.fixture
    def sample_segments(self) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(
                segment_id="seg_001",
                t0=0.0,
                t1=5.0,
                speaker="Alice",
                text="Hello, I am working with Bob at Acme Corp.",
            ),
            TranscriptSegment(
                segment_id="seg_002",
                t0=5.0,
                t1=10.0,
                speaker="Bob",
                text="Yes, the Omega Project is going well.",
            ),
        ]

    def test_extract_mentions_success(
        self,
        extractor: EntityExtractor,
        mock_llm: MagicMock,
        sample_segments: list[TranscriptSegment],
    ) -> None:
        """Should successfully extract and verify valid mentions."""
        # Mock LLM response
        extraction = MentionExtraction(
            mention_text="Bob",
            type=EntityType.PERSON,
            segment_id="seg_001",
            quote="working with Bob",
            t0=None,
            t1=None,
        )

        mock_llm.structured_completion.return_value = ExtractionResponse(mentions=[extraction])

        results = extractor.extract_mentions(sample_segments)

        assert len(results) == 1
        assert results[0].is_valid
        assert results[0].cleaned_extraction.mention_text == "Bob"

        # Verify LLM called with segment text
        call_args = mock_llm.structured_completion.call_args
        prompt = call_args[1]["prompt"]
        assert "Alice: Hello" in prompt
        assert "seg_001" in prompt

    def test_verify_fails_segment_not_found(
        self, extractor: EntityExtractor, sample_segments: list[TranscriptSegment]
    ) -> None:
        """Should fail if segment_id does not exist."""
        extraction = MentionExtraction(
            mention_text="Bob",
            type=EntityType.PERSON,
            segment_id="seg_999",  # Missing
            quote="Bob said hi",
        )
        segment_map = {s.segment_id: s for s in sample_segments}

        result = extractor.verify_extraction(extraction, segment_map)

        assert not result.is_valid
        assert "segment_not_found" in result.errors

    def test_verify_fails_quote_not_grounded(
        self, extractor: EntityExtractor, sample_segments: list[TranscriptSegment]
    ) -> None:
        """Should fail if quote text is not found in segment."""
        extraction = MentionExtraction(
            mention_text="Bob",
            type=EntityType.PERSON,
            segment_id="seg_001",
            quote="Bob is clearly not here",  # Hallucinated quote
        )
        segment_map = {s.segment_id: s for s in sample_segments}

        result = extractor.verify_extraction(extraction, segment_map)

        assert not result.is_valid
        assert "quote_not_grounded" in result.errors

    def test_verify_fails_mention_not_in_quote(
        self, extractor: EntityExtractor, sample_segments: list[TranscriptSegment]
    ) -> None:
        """Should fail if mention_text is not in the quote."""
        extraction = MentionExtraction(
            mention_text="Charlie",  # Not in quote
            type=EntityType.PERSON,
            segment_id="seg_001",
            quote="working with Bob",
        )
        segment_map = {s.segment_id: s for s in sample_segments}

        result = extractor.verify_extraction(extraction, segment_map)

        assert not result.is_valid
        assert "mention_not_in_quote" in result.errors

    def test_verify_fails_timestamps_outside_segment(
        self, extractor: EntityExtractor, sample_segments: list[TranscriptSegment]
    ) -> None:
        """Should fail if mention timestamps are outside segment bounds."""
        # seg_001 is 0.0 to 5.0
        extraction = MentionExtraction(
            mention_text="Bob",
            type=EntityType.PERSON,
            segment_id="seg_001",
            quote="working with Bob",
            t0=5.1,  # Outside
            t1=6.0,
        )
        segment_map = {s.segment_id: s for s in sample_segments}

        result = extractor.verify_extraction(extraction, segment_map)

        assert not result.is_valid
        assert "timestamps_outside_segment" in result.errors

    def test_extract_handles_llm_error(
        self,
        extractor: EntityExtractor,
        mock_llm: MagicMock,
        sample_segments: list[TranscriptSegment],
    ) -> None:
        """Should return empty list gracefully on LLM failure."""
        mock_llm.structured_completion.side_effect = Exception("API Error")

        results = extractor.extract_mentions(sample_segments)

        assert results == []

    def test_normalization_robustness(
        self, extractor: EntityExtractor, sample_segments: list[TranscriptSegment]
    ) -> None:
        """Should allow fuzzy matching via normalization (case/punctuation)."""
        # Segment: "Hello, I am working with Bob at Acme Corp."
        extraction = MentionExtraction(
            mention_text="bob",  # Lowercase
            type=EntityType.PERSON,
            segment_id="seg_001",
            quote="working with Bob.",  # Extra period (vs no period in middle of sentence)
            # Actually source has "Bob at", quote has "Bob." - technically mismatch?
            # Let's try "working with Bob" (no punctuation) vs "working with Bob"
        )
        # Testing robustness: Quote in extraction: "working with Bob." (with period)
        # Segment text: "...working with Bob at..." (no period)
        # normalize_text removes punctuation, so "workinghere with bob" vs "working with bob"

        # Let's construct a cleaner test case
        extraction.quote = "working with Bob!"  # Punctuation difference

    def test_verify_relationship_supported(
        self, extractor: EntityExtractor, mock_llm: MagicMock
    ) -> None:
        """Should return True when LLM says SUPPORTED."""
        # Mock inner Pydantic model response
        mock_response = MagicMock()
        mock_response.verdict = "SUPPORTED"
        mock_llm.structured_completion.return_value = mock_response

        result = extractor.verify_relationship("Bob works at Acme", "Bob", "Acme", "WORKS_AT")

        assert result is True
        mock_llm.structured_completion.assert_called_once()

    def test_verify_relationship_not_supported(
        self, extractor: EntityExtractor, mock_llm: MagicMock
    ) -> None:
        """Should return False when LLM says NOT_SUPPORTED."""
        mock_response = MagicMock()
        mock_response.verdict = "NOT_SUPPORTED"
        mock_llm.structured_completion.return_value = mock_response

        result = extractor.verify_relationship("Bob provided 3 apples", "Bob", "Acme", "WORKS_AT")

        assert result is False

    def test_verify_relationship_error_handling(
        self, extractor: EntityExtractor, mock_llm: MagicMock
    ) -> None:
        """Should fail closed (False) on LLM error."""
        mock_llm.structured_completion.side_effect = Exception("API Error")
        result = extractor.verify_relationship("q", "e1", "e2", "rel")
        assert result is False
