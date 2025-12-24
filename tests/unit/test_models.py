"""Unit tests for Pydantic models."""

import pytest
from pydantic import ValidationError

from src.core.models import (
    AttendeeInfo,
    BlandWebhookPayload,
    CandidateQuery,
    CandidateScore,
    Edge,
    EdgeEvidence,
    EdgeType,
    EntailmentResult,
    Entity,
    EntityStatus,
    EntityType,
    EventContext,
    Mention,
    MentionEvidence,
    MentionExtraction,
    ResolutionState,
    TranscriptSegment,
    TranscriptTurn,
    TriggerPayload,
    VerificationResult,
)


class TestTriggerPayload:
    """Tests for TriggerPayload validation."""

    def test_valid_payload(self):
        """Valid payload should parse correctly."""
        payload = TriggerPayload(
            phone_number="+15551234567",
            event_context=EventContext(
                event_type="meeting_debrief",
                subject="Q4 Planning",
                participants=["Sarah", "Mike"],
            ),
            interview_prompts=["What was discussed?"],
        )
        assert payload.phone_number == "+15551234567"
        assert payload.event_context.subject == "Q4 Planning"

    # === Phone Number E.164 Validation Tests ===

    @pytest.mark.parametrize(
        "phone",
        [
            "+15551234567",  # US
            "+447584019464",  # UK
            "+33612345678",  # France
            "+491701234567",  # Germany
            "+81901234567",  # Japan
            "+8613812345678",  # China (14 digits)
            "+1234567",  # Minimum 7 digits
            "+123456789012345",  # Maximum 15 digits
        ],
    )
    def test_valid_international_phone_numbers(self, phone: str):
        """E.164 international phone numbers should be accepted."""
        payload = TriggerPayload(
            phone_number=phone,
            event_context=EventContext(event_type="general", subject="Test"),
            interview_prompts=["Question?"],
        )
        assert payload.phone_number == phone

    @pytest.mark.parametrize(
        "phone,reason",
        [
            ("555-123-4567", "no country code"),
            ("5551234567", "missing + prefix"),
            ("+0123456789", "starts with 0 after +"),
            ("+123456", "too short (6 digits)"),
            ("+1234567890123456", "too long (16 digits)"),
            ("++15551234567", "double +"),
            ("+1-555-123-4567", "contains dashes"),
            ("+1 555 123 4567", "contains spaces"),
            ("+1(555)1234567", "contains parentheses"),
            ("", "empty string"),
        ],
    )
    def test_invalid_phone_formats(self, phone: str, reason: str):
        """Invalid phone formats should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TriggerPayload(
                phone_number=phone,
                event_context=EventContext(event_type="general", subject="Test"),
                interview_prompts=["Question?"],
            )
        assert "phone_number" in str(exc_info.value), f"Failed for: {reason}"

    def test_empty_prompts_rejected(self):
        """At least one prompt is required."""
        with pytest.raises(ValidationError):
            TriggerPayload(
                phone_number="+15551234567",
                event_context=EventContext(
                    event_type="meeting_debrief",
                    subject="Test",
                ),
                interview_prompts=[],  # Empty list
            )


class TestBlandWebhookPayload:
    """Tests for BlandWebhookPayload validation."""

    def test_valid_webhook(self):
        """Valid webhook payload should parse correctly."""
        payload = BlandWebhookPayload(
            call_id="abc-123",
            status="completed",
            to="+15551234567",
            **{"from": "+18005551234"},  # 'from' is a reserved keyword
            call_length=3.5,
            transcripts=[
                TranscriptTurn(
                    id=1, user="assistant", text="Hello", created_at="2025-01-01T10:00:00Z"
                ),
                TranscriptTurn(
                    id=2, user="user", text="Hi there", created_at="2025-01-01T10:00:05Z"
                ),
            ],
            concatenated_transcript="Assistant: Hello\nUser: Hi there",
        )
        assert payload.call_id == "abc-123"
        assert payload.from_number == "+18005551234"
        assert len(payload.transcripts) == 2
        # Backward compatibility alias
        assert len(payload.transcript) == 2
        # Speaker alias on TranscriptTurn
        assert payload.transcripts[0].speaker == "assistant"
        assert payload.transcripts[0].id == 1
        assert payload.transcripts[0].created_at == "2025-01-01T10:00:00Z"

    def test_from_alias(self):
        """'from' field should be accessible via from_number."""
        payload = BlandWebhookPayload.model_validate(
            {
                "call_id": "test",
                "status": "completed",
                "to": "+15551234567",
                "from": "+18001234567",
            }
        )
        assert payload.from_number == "+18001234567"

    def test_ignores_extra_fields(self):
        """Should ignore unknown fields from Bland API."""
        payload = BlandWebhookPayload.model_validate(
            {
                "call_id": "test",
                "status": "completed",
                "unknown_field": "ignored",
                "transfer_duration": None,
                "another_field": {"nested": "value"},
            }
        )
        assert payload.call_id == "test"

    def test_nested_variables(self):
        """Should accept nested metadata in variables."""
        payload = BlandWebhookPayload.model_validate(
            {
                "call_id": "test",
                "status": "completed",
                "variables": {
                    "metadata": {"event_context": '{"event_type": "general", "subject": "Test"}'}
                },
            }
        )
        assert payload.variables["metadata"]["event_context"] is not None


# === Slice 3: Knowledge Graph Model Tests ===


class TestEntityType:
    """Tests for EntityType enum."""

    def test_entity_types(self):
        """All entity types should be defined."""
        assert EntityType.PERSON.value == "Person"
        assert EntityType.ORGANIZATION.value == "Organization"
        assert EntityType.PROJECT.value == "Project"

    def test_entity_type_is_string_enum(self):
        """EntityType should be usable as string."""
        assert str(EntityType.PERSON) == "EntityType.PERSON"
        assert EntityType.PERSON == "Person"


class TestEntityStatus:
    """Tests for EntityStatus enum."""

    def test_entity_statuses(self):
        """All entity statuses should be defined."""
        assert EntityStatus.RESOLVED.value == "resolved"
        assert EntityStatus.PROVISIONAL.value == "provisional"
        assert EntityStatus.MERGED.value == "merged"


class TestResolutionState:
    """Tests for ResolutionState enum."""

    def test_resolution_states(self):
        """All resolution states should be defined."""
        assert ResolutionState.LINKED.value == "linked"
        assert ResolutionState.AMBIGUOUS.value == "ambiguous"
        assert ResolutionState.NEW_ENTITY_CREATED.value == "new_entity_created"


class TestEdgeType:
    """Tests for EdgeType enum."""

    def test_edge_types(self):
        """All edge types should be defined."""
        assert EdgeType.WORKS_AT.value == "WORKS_AT"
        assert EdgeType.WORKS_ON.value == "WORKS_ON"
        assert EdgeType.RELATES_TO.value == "RELATES_TO"
        assert EdgeType.INTRODUCED.value == "INTRODUCED"


class TestAttendeeInfo:
    """Tests for AttendeeInfo model."""

    def test_attendee_with_email(self):
        """Attendee with name and email."""
        attendee = AttendeeInfo(name="Sam Johnson", email="sam@acme.com")
        assert attendee.name == "Sam Johnson"
        assert attendee.email == "sam@acme.com"

    def test_attendee_without_email(self):
        """Attendee without email should be valid."""
        attendee = AttendeeInfo(name="Sam Johnson")
        assert attendee.name == "Sam Johnson"
        assert attendee.email is None

    def test_attendee_name_required(self):
        """Name is required."""
        with pytest.raises(ValidationError):
            AttendeeInfo(email="sam@acme.com")  # type: ignore


class TestTranscriptSegment:
    """Tests for TranscriptSegment model."""

    def test_valid_segment(self):
        """Valid transcript segment."""
        segment = TranscriptSegment(
            segment_id="seg_001",
            t0=0.0,
            t1=12.5,
            speaker="Alice",
            text="Hello everyone, let's get started.",
        )
        assert segment.segment_id == "seg_001"
        assert segment.t0 == 0.0
        assert segment.t1 == 12.5
        assert segment.speaker == "Alice"
        assert "Hello" in segment.text

    def test_segment_without_speaker(self):
        """Segment without speaker (no diarization)."""
        segment = TranscriptSegment(
            segment_id="seg_001",
            t0=0.0,
            t1=12.5,
            text="Hello everyone.",
        )
        assert segment.speaker is None


class TestMentionEvidence:
    """Tests for MentionEvidence model."""

    def test_valid_evidence(self):
        """Valid mention evidence."""
        evidence = MentionEvidence(
            meeting_id="meet_123",
            segment_id="seg_001",
            t0=5.0,
            t1=8.0,
            quote="Sam from Acme mentioned the project.",
        )
        assert evidence.meeting_id == "meet_123"
        assert evidence.segment_id == "seg_001"
        assert evidence.t0 == 5.0
        assert evidence.t1 == 8.0
        assert "Sam" in evidence.quote


class TestEntity:
    """Tests for Entity model."""

    def test_create_person_entity(self):
        """Create a Person entity."""
        entity = Entity(
            user_id="user_123",
            type=EntityType.PERSON,
            display_name="Sam Johnson",
            primary_email="sam@acme.com",
            aliases=["Sam", "Samuel", "sam@acme.com"],
        )
        assert entity.entity_id  # Auto-generated UUID
        assert entity.user_id == "user_123"
        assert entity.type == EntityType.PERSON
        assert entity.display_name == "Sam Johnson"
        assert entity.primary_email == "sam@acme.com"
        assert "Sam" in entity.aliases
        assert entity.status == EntityStatus.PROVISIONAL  # Default

    def test_create_organization_entity(self):
        """Create an Organization entity."""
        entity = Entity(
            user_id="user_123",
            type=EntityType.ORGANIZATION,
            display_name="Acme Corp",
            status=EntityStatus.RESOLVED,
        )
        assert entity.type == EntityType.ORGANIZATION
        assert entity.status == EntityStatus.RESOLVED

    def test_merged_entity_tombstone(self):
        """Entity marked as merged (tombstone)."""
        entity = Entity(
            user_id="user_123",
            type=EntityType.PERSON,
            display_name="Sam (old)",
            status=EntityStatus.MERGED,
            merged_into="entity_456",
            merged_at="2024-01-15T10:00:00Z",
        )
        assert entity.status == EntityStatus.MERGED
        assert entity.merged_into == "entity_456"
        assert entity.merged_at is not None

    def test_entity_defaults(self):
        """Entity has sensible defaults."""
        entity = Entity(
            user_id="user_123",
            type=EntityType.PERSON,
            display_name="Sam",
        )
        assert entity.aliases == []
        assert entity.status == EntityStatus.PROVISIONAL
        assert entity.mention_count == 0
        assert entity.edge_count == 0
        assert entity.recent_meeting_ids == []
        assert entity.top_evidence == []
        assert entity.created_at  # Auto-generated
        assert entity.updated_at  # Auto-generated


class TestMention:
    """Tests for Mention model."""

    def test_create_mention(self):
        """Create a mention."""
        evidence = MentionEvidence(
            meeting_id="meet_123",
            segment_id="seg_001",
            t0=5.0,
            t1=8.0,
            quote="Sam from Acme mentioned the project.",
        )
        mention = Mention(
            user_id="user_123",
            mention_text="Sam",
            type=EntityType.PERSON,
            local_context="Sam from Acme mentioned the project. He's the CFO.",
            evidence=evidence,
            role_hint="CFO",
            org_hint="Acme",
        )
        assert mention.mention_id  # Auto-generated
        assert mention.mention_text == "Sam"
        assert mention.type == EntityType.PERSON
        assert mention.role_hint == "CFO"
        assert mention.org_hint == "Acme"
        assert mention.resolution_state == ResolutionState.AMBIGUOUS  # Default

    def test_linked_mention(self):
        """Mention that's been linked to an entity."""
        evidence = MentionEvidence(
            meeting_id="meet_123",
            segment_id="seg_001",
            t0=5.0,
            t1=8.0,
            quote="Sam mentioned the project.",
        )
        mention = Mention(
            user_id="user_123",
            mention_text="Sam",
            type=EntityType.PERSON,
            local_context="Sam mentioned the project.",
            evidence=evidence,
            resolution_state=ResolutionState.LINKED,
            linked_entity_id="entity_456",
            confidence=0.95,
            verified=True,
        )
        assert mention.resolution_state == ResolutionState.LINKED
        assert mention.linked_entity_id == "entity_456"
        assert mention.confidence == 0.95
        assert mention.verified is True

    def test_ambiguous_mention(self):
        """Mention with multiple candidates."""
        evidence = MentionEvidence(
            meeting_id="meet_123",
            segment_id="seg_001",
            t0=5.0,
            t1=8.0,
            quote="Sam mentioned the project.",
        )
        mention = Mention(
            user_id="user_123",
            mention_text="Sam",
            type=EntityType.PERSON,
            local_context="Sam mentioned the project.",
            evidence=evidence,
            resolution_state=ResolutionState.AMBIGUOUS,
            candidate_entity_ids=["entity_1", "entity_2"],
            candidate_scores=[
                {"entity_id": "entity_1", "score": 0.7, "reasoning": "Name match"},
                {"entity_id": "entity_2", "score": 0.65, "reasoning": "Recent meeting"},
            ],
        )
        assert mention.resolution_state == ResolutionState.AMBIGUOUS
        assert mention.linked_entity_id is None
        assert len(mention.candidate_entity_ids) == 2
        assert len(mention.candidate_scores) == 2


class TestEdge:
    """Tests for Edge model."""

    def test_works_at_edge(self):
        """Create a WORKS_AT edge."""
        edge = Edge(
            user_id="user_123",
            from_entity_id="person_1",
            to_entity_id="org_1",
            edge_type=EdgeType.WORKS_AT,
            meeting_id="meet_123",
            evidence=[
                EdgeEvidence(
                    meeting_id="meet_123",
                    quote="Sam works at Acme.",
                    t0=5.0,
                    t1=8.0,
                )
            ],
            confidence=0.95,
            verified=True,
        )
        assert edge.edge_type == EdgeType.WORKS_AT
        assert edge.from_entity_id == "person_1"
        assert edge.to_entity_id == "org_1"
        assert len(edge.evidence) == 1
        assert edge.verified is True

    def test_relates_to_edge_with_label(self):
        """Create a RELATES_TO edge with label property."""
        edge = Edge(
            user_id="user_123",
            from_entity_id="person_1",
            to_entity_id="person_2",
            edge_type=EdgeType.RELATES_TO,
            meeting_id="meet_123",
            properties={"label": "advisor"},
            evidence=[],
        )
        assert edge.edge_type == EdgeType.RELATES_TO
        assert edge.properties["label"] == "advisor"


class TestMentionExtraction:
    """Tests for MentionExtraction model."""

    def test_valid_extraction(self):
        """Valid LLM extraction output."""
        extraction = MentionExtraction(
            mention_text="Sam",
            type=EntityType.PERSON,
            segment_id="seg_001",
            quote="Sam from Acme mentioned the project.",
            t0=5.0,
            t1=8.0,
            role_hint="CFO",
            org_hint="Acme",
        )
        assert extraction.mention_text == "Sam"
        assert extraction.type == EntityType.PERSON
        assert extraction.segment_id == "seg_001"
        assert extraction.role_hint == "CFO"

    def test_extraction_without_hints(self):
        """Extraction without optional hints."""
        extraction = MentionExtraction(
            mention_text="Acme Corp",
            type=EntityType.ORGANIZATION,
            segment_id="seg_002",
            quote="We're working with Acme Corp.",
        )
        assert extraction.role_hint is None
        assert extraction.org_hint is None
        assert extraction.t0 is None


class TestVerificationResult:
    """Tests for VerificationResult model."""

    def test_valid_verification(self):
        """Successful verification."""
        extraction = MentionExtraction(
            mention_text="Sam",
            type=EntityType.PERSON,
            segment_id="seg_001",
            quote="Sam mentioned the project.",
        )
        result = VerificationResult(
            is_valid=True,
            cleaned_extraction=extraction,
            errors=[],
            warnings=["quote_too_long"],
        )
        assert result.is_valid is True
        assert result.cleaned_extraction is not None
        assert len(result.warnings) == 1

    def test_failed_verification(self):
        """Failed verification (blocking error)."""
        result = VerificationResult(
            is_valid=False,
            cleaned_extraction=None,
            errors=["quote_not_grounded", "mention_not_in_quote"],
            warnings=[],
        )
        assert result.is_valid is False
        assert result.cleaned_extraction is None
        assert len(result.errors) == 2


class TestEntailmentResult:
    """Tests for EntailmentResult model."""

    def test_supported_verdict(self):
        """SUPPORTED verdict."""
        result = EntailmentResult(
            verdict="SUPPORTED",
            rationale="The quote explicitly states Sam works at Acme.",
        )
        assert result.verdict == "SUPPORTED"
        assert "explicitly" in result.rationale

    def test_ambiguous_verdict(self):
        """AMBIGUOUS verdict."""
        result = EntailmentResult(
            verdict="AMBIGUOUS",
            rationale="The quote implies but doesn't confirm the relationship.",
        )
        assert result.verdict == "AMBIGUOUS"

    def test_not_supported_verdict(self):
        """NOT_SUPPORTED verdict."""
        result = EntailmentResult(
            verdict="NOT_SUPPORTED",
            rationale="The quote doesn't mention this relationship.",
        )
        assert result.verdict == "NOT_SUPPORTED"


class TestCandidateQuery:
    """Tests for CandidateQuery model."""

    def test_full_query(self):
        """Query with all context."""
        query = CandidateQuery(
            mention_text="Sam",
            meeting_id="meet_123",
            meeting_attendees=[
                AttendeeInfo(name="Samuel Johnson", email="sam@acme.com"),
                AttendeeInfo(name="Jane Doe", email="jane@acme.com"),
            ],
            local_context="Sam from Acme mentioned the project.",
            role_hint="CFO",
            speaker_email="sam@acme.com",
        )
        assert query.mention_text == "Sam"
        assert len(query.meeting_attendees) == 2
        assert query.role_hint == "CFO"

    def test_minimal_query(self):
        """Query with minimal context."""
        query = CandidateQuery(
            mention_text="Acme",
            meeting_id="meet_123",
        )
        assert query.meeting_attendees == []
        assert query.role_hint is None


class TestCandidateScore:
    """Tests for CandidateScore model."""

    def test_high_confidence_score(self):
        """High confidence candidate score."""
        score = CandidateScore(
            entity_id="entity_123",
            score=0.92,
            confidence="HIGH",
            reasoning="Name and organization match strongly.",
        )
        assert score.entity_id == "entity_123"
        assert score.score == 0.92
        assert score.confidence == "HIGH"

    def test_low_confidence_score(self):
        """Low confidence candidate score."""
        score = CandidateScore(
            entity_id="entity_456",
            score=0.35,
            confidence="LOW",
            reasoning="Name partially matches but context differs.",
        )
        assert score.confidence == "LOW"
        assert score.score < 0.5
