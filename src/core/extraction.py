"""Entity extraction and verification logic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    # Support both Lambda and test import paths
    try:
        from core.interfaces import LLMClient
    except ImportError:
        from src.core.interfaces import LLMClient

# Support both Lambda and test import paths
try:
    from core.models import (
        MentionExtraction,
        TranscriptSegment,
        VerificationResult,
        normalize_text,
    )
except ImportError:
    from src.core.models import (
        MentionExtraction,
        TranscriptSegment,
        VerificationResult,
        normalize_text,
    )

logger = logging.getLogger(__name__)


class ExtractionResponse(BaseModel):
    """Container for LLM extraction output."""

    mentions: list[MentionExtraction]


class EntityExtractor:
    """Service for extracting and validating entities from transcripts."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    def extract_mentions(self, segments: list[TranscriptSegment]) -> list[VerificationResult]:
        """Extract and verify mentions from transcript segments."""
        if not segments:
            return []

        # 1. Prepare context for LLM
        transcript_text = "\n".join(
            f"[{seg.segment_id}] {seg.speaker or 'Unknown'}: {seg.text}" for seg in segments
        )

        # 2. Call LLM for extraction
        prompt = self._build_extraction_prompt(transcript_text)
        try:
            response = self.llm.structured_completion(
                prompt=prompt,
                output_model=ExtractionResponse,
                system_prompt="Extract entities (People, Organizations, Projects) from the transcript.",
            )
            raw_extractions = response.mentions
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")
            return []

        # 3. Verify extractions
        results: list[VerificationResult] = []
        segment_map = {s.segment_id: s for s in segments}

        for extraction in raw_extractions:
            result = self.verify_extraction(extraction, segment_map)
            results.append(result)

        return results

    def verify_extraction(
        self, extraction: MentionExtraction, segment_map: dict[str, TranscriptSegment]
    ) -> VerificationResult:
        """Verify an extraction against the source transcript."""
        errors: list[str] = []
        warnings: list[str] = []
        cleaned = extraction.model_copy()

        # 1. Segment Existence Check
        segment = segment_map.get(extraction.segment_id)
        if not segment:
            errors.append("segment_not_found")
            return VerificationResult(is_valid=False, errors=errors)

        # 2. Quote Grounding (Semantic/Normalization check)
        # We verify if the quote actually appears in the segment text
        # utilizing normalization to be robust against minor punctuation diffs
        norm_quote = normalize_text(extraction.quote)
        norm_text = normalize_text(segment.text)

        if norm_quote not in norm_text:
            errors.append("quote_not_grounded")

        # 3. Mention in Quote Check
        norm_mention = normalize_text(extraction.mention_text)
        if norm_mention not in norm_quote:
            errors.append("mention_not_in_quote")

        # 4. Timestamp Validation
        if extraction.t0 is not None and extraction.t1 is not None:
            if extraction.t0 < segment.t0 or extraction.t1 > segment.t1:
                # Allow small epsilon or logging, but strictly:
                errors.append("timestamps_outside_segment")

            if extraction.t1 < extraction.t0:
                errors.append("invalid_timestamps")

        # 5. Role/Org Verification (optional - LLM based usually, doing simple check here)
        # Plan says LLM verified, but for now we trust extraction unless blank
        if extraction.role_hint == "":
            cleaned.role_hint = None
        if extraction.org_hint == "":
            cleaned.org_hint = None

        is_valid = len(errors) == 0

        return VerificationResult(
            is_valid=is_valid,
            cleaned_extraction=cleaned if is_valid else None,
            errors=errors,
            warnings=warnings,
        )

    def verify_relationship(
        self, quote: str, from_entity: str, to_entity: str, relationship_type: str
    ) -> bool:
        """Verify if a quote supports a relationship claim (Entailment check)."""
        prompt = f"""
Does this quote support the claim?

Quote: "{quote}"
Claim: {from_entity} {relationship_type} {to_entity}

Respond with ONLY a JSON object:
{{
  "verdict": "SUPPORTED" | "NOT_SUPPORTED" | "AMBIGUOUS",
  "rationale": "one sentence explanation"
}}

Rules:
- SUPPORTED: The quote directly and explicitly supports the claim
- NOT_SUPPORTED: The quote contradicts or does not mention this relationship
- AMBIGUOUS: The quote is unclear or only implies the relationship
"""
        try:
            # We use a local Pydantic model for the LLM response
            class EntailmentResponse(BaseModel):
                verdict: str
                rationale: str

            response = self.llm.structured_completion(
                prompt=prompt,
                output_model=EntailmentResponse,
                system_prompt="You are a strict logic verifier.",
            )

            is_supported: bool = response.verdict == "SUPPORTED"
            return is_supported

        except Exception as e:
            logger.error(f"Entailment check failed: {e}")
            # Fail closed for safety against hallucinations
            return False

    def _build_extraction_prompt(self, transcript_text: str) -> str:
        """Build the extraction prompt."""
        return f"""
Analyze the following meeting transcript and extract entities.

Target Entities:
1. PERSON: Named individuals (attendees, mentioned people).
2. ORGANIZATION: Companies, teams, institutions.
3. PROJECT: Named initiatives, products, workstreams.

For each extraction, you MUST provide:
- segment_id: The ID of the line where it appears (e.g. seg_001)
- quote: The EXACT phrase where the entity is mentioned
- mention_text: The entity name as it appears in the quote
- type: One of "Person", "Organization", "Project"
- role_hint: (Optional) Used for resolving "Sarah from Marketing" -> 'Marketing'
- org_hint: (Optional) Used for resolving "Jeff from Amazon" -> 'Amazon'

Transcript:
{transcript_text}
"""
