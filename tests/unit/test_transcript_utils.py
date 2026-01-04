"""Unit tests for transcript utilities."""

from __future__ import annotations

from src.core.models import TranscriptTurn
from src.core.transcript_utils import convert_bland_transcript, normalize_text


class TestNormalizeText:
    """Tests for normalize_text function."""

    def test_lowercases_text(self) -> None:
        """Should convert text to lowercase."""
        assert normalize_text("Hello WORLD") == "hello world"

    def test_removes_punctuation_except_apostrophes(self) -> None:
        """Should remove punctuation but keep apostrophes in contractions."""
        assert normalize_text("It's a test! How's that?") == "it's a test how's that"

    def test_collapses_whitespace(self) -> None:
        """Should collapse multiple spaces to single space."""
        assert normalize_text("hello    world") == "hello world"
        assert normalize_text("hello\t\nworld") == "hello world"

    def test_strips_leading_trailing_whitespace(self) -> None:
        """Should strip leading and trailing whitespace."""
        assert normalize_text("  hello world  ") == "hello world"

    def test_removes_diarization_tags(self) -> None:
        """Should remove speaker diarization tags like [Speaker 1]:."""
        assert normalize_text("[Speaker 1]: Hello there") == "hello there"
        assert normalize_text("[speaker 2]: Hi") == "hi"
        assert normalize_text("[Speaker]: Test") == "test"

    def test_handles_empty_string(self) -> None:
        """Should handle empty string."""
        assert normalize_text("") == ""

    def test_handles_only_punctuation(self) -> None:
        """Should handle string with only punctuation."""
        assert normalize_text("!!!...???") == ""

    def test_preserves_numbers(self) -> None:
        """Should preserve numbers in text."""
        assert normalize_text("I have 3 meetings at 10:30") == "i have 3 meetings at 10 30"

    def test_handles_unicode(self) -> None:
        """Should handle unicode characters appropriately."""
        # Unicode letters should be preserved, normalized
        assert normalize_text("Café résumé") == "café résumé"


class TestConvertBlandTranscript:
    """Tests for convert_bland_transcript function."""

    def test_converts_single_turn(self) -> None:
        """Should convert a single transcript turn."""
        turns = [
            TranscriptTurn(
                id=1,
                user="user",
                text="Hello, this is a test.",
                created_at="2024-01-15T10:00:00Z",
            )
        ]

        segments = convert_bland_transcript(turns)

        assert len(segments) == 1
        assert segments[0].segment_id == "seg_1"
        assert segments[0].text == "Hello, this is a test."
        assert segments[0].speaker == "user"
        assert segments[0].t0 == 0.0

    def test_converts_multiple_turns_with_timing(self) -> None:
        """Should convert multiple turns with relative timing."""
        turns = [
            TranscriptTurn(
                id=1,
                user="assistant",
                text="Hello!",
                created_at="2024-01-15T10:00:00Z",
            ),
            TranscriptTurn(
                id=2,
                user="user",
                text="Hi there.",
                created_at="2024-01-15T10:00:05Z",
            ),
            TranscriptTurn(
                id=3,
                user="assistant",
                text="How can I help?",
                created_at="2024-01-15T10:00:10Z",
            ),
        ]

        segments = convert_bland_transcript(turns)

        assert len(segments) == 3
        # First segment starts at 0
        assert segments[0].t0 == 0.0
        assert segments[0].t1 == 5.0
        # Second segment starts at 5 seconds
        assert segments[1].t0 == 5.0
        assert segments[1].t1 == 10.0
        # Third segment starts at 10 seconds
        assert segments[2].t0 == 10.0

    def test_handles_empty_transcript(self) -> None:
        """Should handle empty transcript list."""
        segments = convert_bland_transcript([])

        assert len(segments) == 0

    def test_uses_bland_id_for_segment_id(self) -> None:
        """Should use Bland's ID to construct segment_id."""
        turns = [
            TranscriptTurn(
                id=42,
                user="user",
                text="Test",
                created_at="2024-01-15T10:00:00Z",
            )
        ]

        segments = convert_bland_transcript(turns)

        assert segments[0].segment_id == "seg_42"

    def test_maps_speaker_correctly(self) -> None:
        """Should map Bland's 'user' field to speaker."""
        turns = [
            TranscriptTurn(id=1, user="assistant", text="Hi", created_at="2024-01-15T10:00:00Z"),
            TranscriptTurn(id=2, user="user", text="Hello", created_at="2024-01-15T10:00:01Z"),
            TranscriptTurn(
                id=3, user="agent-action", text="[action]", created_at="2024-01-15T10:00:02Z"
            ),
        ]

        segments = convert_bland_transcript(turns)

        assert segments[0].speaker == "assistant"
        assert segments[1].speaker == "user"
        assert segments[2].speaker == "agent-action"

    def test_handles_same_timestamp_turns(self) -> None:
        """Should handle turns with same timestamp gracefully."""
        turns = [
            TranscriptTurn(id=1, user="user", text="First", created_at="2024-01-15T10:00:00Z"),
            TranscriptTurn(
                id=2, user="assistant", text="Second", created_at="2024-01-15T10:00:00Z"
            ),
        ]

        segments = convert_bland_transcript(turns)

        assert len(segments) == 2
        assert segments[0].t0 == 0.0
        assert segments[1].t0 == 0.0

    def test_last_segment_has_estimated_end_time(self) -> None:
        """Should estimate end time for last segment based on text length."""
        turns = [
            TranscriptTurn(
                id=1,
                user="user",
                text="This is a longer piece of text that should take some time to say.",
                created_at="2024-01-15T10:00:00Z",
            )
        ]

        segments = convert_bland_transcript(turns)

        # Last segment should have t1 > t0
        assert segments[0].t1 > segments[0].t0

    def test_handles_timezone_aware_timestamps(self) -> None:
        """Should handle various ISO timestamp formats."""
        turns = [
            TranscriptTurn(
                id=1,
                user="user",
                text="Hello",
                created_at="2024-01-15T10:00:00+00:00",
            ),
            TranscriptTurn(
                id=2,
                user="assistant",
                text="Hi",
                created_at="2024-01-15T10:00:05+00:00",
            ),
        ]

        segments = convert_bland_transcript(turns)

        assert len(segments) == 2
        assert segments[1].t0 == 5.0
