"""Transcript utilities for text normalization and format conversion.

This module provides utilities for:
1. Normalizing transcript text for comparison (verification)
2. Converting Bland AI transcript format to our internal TranscriptSegment format
"""

from __future__ import annotations

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


def convert_bland_transcript(turns: list[TranscriptTurn]) -> list[TranscriptSegment]:
    """Convert Bland AI transcript turns to our internal TranscriptSegment format.

    Bland provides turns with:
    - id: unique segment ID
    - user: speaker ("assistant", "user", "agent-action")
    - text: the spoken text
    - created_at: ISO timestamp when segment was spoken

    We convert to TranscriptSegment with:
    - segment_id: "seg_{id}"
    - t0/t1: relative timestamps in seconds from call start
    - speaker: mapped from user field
    - text: unchanged

    Args:
        turns: List of TranscriptTurn from Bland webhook

    Returns:
        List of TranscriptSegment with relative timing
    """
    if not turns:
        return []

    segments: list[TranscriptSegment] = []

    # Parse all timestamps first
    timestamps: list[datetime] = []
    for turn in turns:
        try:
            # Handle various ISO formats
            ts = datetime.fromisoformat(turn.created_at.replace("Z", "+00:00"))
            timestamps.append(ts)
        except (ValueError, AttributeError):
            # Fallback: use None, will handle below
            timestamps.append(None)  # type: ignore[arg-type]

    # Calculate base time (first segment starts at 0)
    base_time = timestamps[0] if timestamps and timestamps[0] else None

    for i, turn in enumerate(turns):
        # Calculate t0 (relative to first segment)
        t0 = (timestamps[i] - base_time).total_seconds() if base_time and timestamps[i] else 0.0

        # Calculate t1 (start of next segment, or estimated from text length)
        if i + 1 < len(turns) and timestamps[i + 1] and base_time:
            t1 = (timestamps[i + 1] - base_time).total_seconds()
        else:
            # Estimate based on average speaking rate (~150 words/min = 2.5 words/sec)
            word_count = len(turn.text.split())
            estimated_duration = max(1.0, word_count / 2.5)  # At least 1 second
            t1 = t0 + estimated_duration

        segment = TranscriptSegment(
            segment_id=f"seg_{turn.id}",
            t0=t0,
            t1=t1,
            speaker=turn.user,
            text=turn.text,
        )
        segments.append(segment)

    return segments
