"""Entity resolution service."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

# Support both Lambda and test import paths
try:
    from core.models import (
        Mention,
        MentionEvidence,
        MentionExtraction,
        ResolutionState,
        TranscriptSegment,
    )
except ImportError:
    from src.core.models import (
        Mention,
        MentionEvidence,
        MentionExtraction,
        ResolutionState,
        TranscriptSegment,
    )

if TYPE_CHECKING:
    try:
        from core.extraction import EntityExtractor
        from core.interfaces import (
            EntitiesRepositoryProtocol,
            MentionsRepositoryProtocol,
            TranscriptsRepositoryProtocol,
        )
    except ImportError:
        from src.core.extraction import EntityExtractor
        from src.core.interfaces import (
            EntitiesRepositoryProtocol,
            MentionsRepositoryProtocol,
            TranscriptsRepositoryProtocol,
        )

logger = logging.getLogger(__name__)


class EntityResolutionService:
    """Service for resolving extracted mentions to entities."""

    def __init__(
        self,
        extractor: EntityExtractor,
        entities_repo: EntitiesRepositoryProtocol,
        mentions_repo: MentionsRepositoryProtocol,
        transcripts_repo: TranscriptsRepositoryProtocol,
    ) -> None:
        self.extractor = extractor
        self.entities_repo = entities_repo
        self.mentions_repo = mentions_repo
        self.transcripts_repo = transcripts_repo

    def process_meeting(self, user_id: str, meeting_id: str) -> None:
        """Process a meeting: extract and resolve mentions."""
        # 1. Fetch transcript
        segments = self.transcripts_repo.get_transcript(user_id, meeting_id)
        if not segments:
            logger.info(f"No transcript found for meeting {meeting_id}")
            return

        # 2. Extract mentions (verified)
        results = self.extractor.extract_mentions(segments)
        segment_map = {s.segment_id: s for s in segments}

        # 3. Process each verified extraction
        for result in results:
            if not result.is_valid or not result.cleaned_extraction:
                continue

            segment = segment_map.get(result.cleaned_extraction.segment_id)
            if not segment:
                continue

            self.resolve_mention(user_id, meeting_id, result.cleaned_extraction, segment)

    def resolve_mention(
        self,
        user_id: str,
        meeting_id: str,
        extraction: MentionExtraction,
        segment: TranscriptSegment,
    ) -> Mention:
        """Resolve a single mention to an entity."""

        # Use specific timestamps if available, else segment timestamps
        t0 = extraction.t0 if extraction.t0 is not None else segment.t0
        t1 = extraction.t1 if extraction.t1 is not None else segment.t1

        # 1. Create Mention record (unlinked initially)
        mention_id = str(uuid4())
        mention = Mention(
            mention_id=mention_id,
            user_id=user_id,
            mention_text=extraction.mention_text,
            type=extraction.type,
            resolution_state=ResolutionState.AMBIGUOUS,
            local_context=segment.text,  # Use full segment text as context
            evidence=MentionEvidence(
                meeting_id=meeting_id,
                segment_id=extraction.segment_id,
                t0=t0,
                t1=t1,
                quote=extraction.quote,
            ),
        )
        self.mentions_repo.create_mention(mention)

        # 2. Exact Alias Network Search
        # Check if we already know this alias
        candidate_ids = self.entities_repo.query_by_alias(user_id, extraction.mention_text)

        if candidate_ids:
            # Found exact match(es)
            # For MVP/Slice 3, if exact alias match, we pick the first one (greedy)
            # A more robust system would handle multiple exact matches (homonyms) as ambiguous
            entity_id = candidate_ids[0]

            # Link mention
            self.mentions_repo.mark_linked(user_id, mention_id, entity_id, confidence=1.0)
            mention.resolution_state = ResolutionState.LINKED
            mention.linked_entity_id = entity_id
            return mention

        # 3. No match -> Create Provisional Entity
        entity = self.entities_repo.create_provisional(
            user_id, extraction.mention_text, extraction.type
        )

        # Link mention to new entity
        self.mentions_repo.mark_linked(user_id, mention_id, entity.entity_id, confidence=1.0)
        mention.resolution_state = ResolutionState.LINKED
        mention.linked_entity_id = entity.entity_id

        return mention
