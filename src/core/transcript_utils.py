"""Transcript utilities for text normalization and format conversion.

This module provides utilities for:
1. Normalizing transcript text for comparison (verification)
2. Converting Bland AI transcript format to our internal TranscriptSegment format
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime

# Support both Lambda (core.models) and test (src.core.models) import paths
try:
    from core.models import TranscriptSegment, TranscriptTurn
except ImportError:
    from src.core.models import TranscriptSegment, TranscriptTurn


def normalize_text(text: str) -> str:
    """Normalize text for comparison during verification.

    Handles:
    - Case folding (lowercase)
    - Punctuation removal (except apostrophes in contractions)
    - Whitespace collapse (multiple spaces → single space)
    - Diarization tag removal (e.g., "[Speaker 1]:")

    Args:
        text: The raw text to normalize

    Returns:
        Normalized text suitable for comparison
    """
    if not text:
        return ""

    # Lowercase
    text = text.lower()

    # Remove diarization tags like [Speaker 1]: or [speaker]:
    text = re.sub(r"\[speaker\s*\d*\]:?\s*", "", text, flags=re.IGNORECASE)

    # Remove punctuation except apostrophes (keep contractions like "it's", "don't")
    # Replace punctuation with space to avoid joining words
    text = re.sub(r"[^\w\s']", " ", text)

    # Collapse whitespace (spaces, tabs, newlines) to single space
    text = re.sub(r"\s+", " ", text)

    # Strip leading/trailing whitespace
    text = text.strip()

    return text


def convert_bland_transcript(
    turns: list[TranscriptTurn],
    call_start_time: str | None = None,
) -> list[TranscriptSegment]:
    """Convert Bland AI transcript turns to our internal TranscriptSegment format.

    Bland provides turns with:
    - id: unique segment ID
    - user: speaker ("assistant", "user", "agent-action")
    - text: the spoken text
    - created_at: ISO timestamp when segment was spoken

    We convert to TranscriptSegment with:
    - segment_id: "seg_{id:04d}" (zero-padded)
    - t0/t1: relative timestamps in seconds from call start
    - speaker: mapped from user field
    - text: unchanged

    Args:
        turns: List of TranscriptTurn from Bland webhook
        call_start_time: Optional ISO timestamp when call started. If provided,
            timestamps are calculated relative to this. If None and turns have
            created_at timestamps, uses first turn's timestamp as base.

    Returns:
        List of TranscriptSegment with relative timing
    """
    if not turns:
        return []

    segments: list[TranscriptSegment] = []

    # Determine base time for relative timestamp calculation
    # Only calculate timestamps if call_start_time is explicitly provided
    base_time: datetime | None = None

    if call_start_time:
        with contextlib.suppress(ValueError, AttributeError):
            base_time = datetime.fromisoformat(call_start_time.replace("Z", "+00:00"))

    # Parse all turn timestamps (only if we have a valid base_time)
    timestamps: list[datetime | None] = []
    if base_time:
        for turn in turns:
            try:
                if turn.created_at:
                    ts = datetime.fromisoformat(turn.created_at.replace("Z", "+00:00"))
                    timestamps.append(ts)
                else:
                    timestamps.append(None)
            except (ValueError, AttributeError):
                timestamps.append(None)
    else:
        timestamps = [None] * len(turns)

    for i, turn in enumerate(turns):
        # Generate zero-padded segment ID
        segment_id = f"seg_{turn.id:04d}"

        # Calculate t0 (relative to base time)
        t0 = 0.0
        t1 = 0.0

        if base_time and timestamps[i] is not None:
            ts_i = timestamps[i]
            assert ts_i is not None  # For mypy
            t0 = (ts_i - base_time).total_seconds()

            # Calculate t1: use next segment's start time if available
            if i + 1 < len(turns) and timestamps[i + 1] is not None:
                ts_next = timestamps[i + 1]
                assert ts_next is not None  # For mypy
                t1 = (ts_next - base_time).total_seconds()
            else:
                # Estimate based on word count (~150 words/min = 2.5 words/sec)
                word_count = len(turn.text.split())
                estimated_duration = (word_count / 150) * 60  # Convert to seconds
                t1 = t0 + estimated_duration

        segment = TranscriptSegment(
            segment_id=segment_id,
            t0=t0,
            t1=t1,
            speaker=turn.user,
            text=turn.text,
        )
        segments.append(segment)

    return segments
