"""Unit tests for Outlook Calendar webhook Lambda handler (Phase 4C.3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.models import KairosCalendarEvent
from src.handlers.outlook_calendar_webhook import handler


@pytest.fixture
def mock_env(monkeypatch):
    """Set required environment variables."""
    monkeypatch.setenv("CALENDAR_EVENTS_TABLE", "test-calendar-events")
    monkeypatch.setenv("CALENDAR_SYNC_STATE_TABLE", "test-sync-state")


@pytest.fixture
def mock_dependencies():
    """Mock all repository and client dependencies."""
    with (
        patch(
            "src.handlers.outlook_calendar_webhook.get_calendar_events_repo"
        ) as mock_events_repo,
        patch(
            "src.handlers.outlook_calendar_webhook.get_calendar_sync_state_repo"
        ) as mock_sync_repo,
        patch(
            "src.handlers.outlook_calendar_webhook.get_microsoft_graph_client"
        ) as mock_graph_client,
    ):
        # Setup mock repositories
        events_repo = MagicMock()
        sync_repo = MagicMock()
        graph_client = MagicMock()

        mock_events_repo.return_value = events_repo
        mock_sync_repo.return_value = sync_repo
        mock_graph_client.return_value = graph_client

        yield {
            "events_repo": events_repo,
            "sync_repo": sync_repo,
            "graph_client": graph_client,
        }


def test_validation_token_handshake(mock_env, mock_dependencies):
    """Test validation token handshake during subscription setup.

    When Microsoft sends ?validationToken=xxx, respond with token in plain text.
    """
    event = {
        "queryStringParameters": {"validationToken": "test-validation-token-123"},
        "requestContext": {"requestId": "test-request-id"},
    }

    result = handler(event, None)

    assert result["statusCode"] == 200
    assert result["headers"]["Content-Type"] == "text/plain"
    assert result["body"] == "test-validation-token-123"


def test_missing_validation_token_continues_to_notification_handler(
    mock_env, mock_dependencies
):
    """Test that absence of validationToken allows notification processing.

    Should NOT return early; should attempt to process as notification.
    """
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.return_value = None

    event = {
        "body": json.dumps({"value": [{"subscriptionId": "sub123"}]}),
        "requestContext": {"requestId": "test-request-id"},
    }

    result = handler(event, None)

    # Should reach subscription lookup (not return early)
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.assert_called_once()
    assert result["statusCode"] == 401  # Unknown subscription


def test_subscription_not_found(mock_env, mock_dependencies):
    """Test early rejection when subscription_id is unknown (O(1) routing)."""
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.return_value = None

    notification_body = {
        "value": [
            {
                "subscriptionId": "unknown-sub-123",
                "clientState": "some-state",
                "resource": "users/me/events",
            }
        ]
    }
    event = {
        "body": json.dumps(notification_body),
        "requestContext": {"requestId": "test-request-id"},
    }

    result = handler(event, None)

    assert result["statusCode"] == 401
    response_body = json.loads(result["body"])
    assert "unknown subscription" in response_body["error"].lower()


def test_client_state_verification_valid_current(mock_env, mock_dependencies):
    """Test clientState verification with current (valid) state."""
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.return_value = {
        "user_id": "user123",
        "provider": "microsoft",
        "client_state": "valid-state-456",
    }
    mock_dependencies["sync_repo"].verify_microsoft_client_state.return_value = True
    mock_dependencies["graph_client"].delta_sync.return_value = ([], "new-delta-link")

    notification_body = {
        "value": [
            {
                "subscriptionId": "sub123",
                "clientState": "valid-state-456",
                "resource": "users/me/events",
            }
        ]
    }
    event = {
        "body": json.dumps(notification_body),
        "requestContext": {"requestId": "test-request-id"},
    }

    result = handler(event, None)

    assert result["statusCode"] == 200
    mock_dependencies["sync_repo"].verify_microsoft_client_state.assert_called_once_with(
        "sub123", "valid-state-456"
    )


def test_client_state_verification_valid_previous_within_window(
    mock_env, mock_dependencies
):
    """Test clientState verification with previous state (within overlap window)."""
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.return_value = {
        "user_id": "user123",
        "provider": "microsoft",
        "client_state": "new-state-789",
        "previous_client_state": "old-state-456",
        "previous_client_state_expires": expires_at.isoformat(),
    }
    mock_dependencies["sync_repo"].verify_microsoft_client_state.return_value = True
    mock_dependencies["graph_client"].delta_sync.return_value = ([], "new-delta-link")

    notification_body = {
        "value": [
            {
                "subscriptionId": "sub123",
                "clientState": "old-state-456",  # Previous state
                "resource": "users/me/events",
            }
        ]
    }
    event = {
        "body": json.dumps(notification_body),
        "requestContext": {"requestId": "test-request-id"},
    }

    result = handler(event, None)

    assert result["statusCode"] == 200


def test_client_state_verification_invalid(mock_env, mock_dependencies):
    """Test clientState verification with invalid state (early rejection)."""
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.return_value = {
        "user_id": "user123",
        "provider": "microsoft",
        "client_state": "valid-state-456",
    }
    mock_dependencies["sync_repo"].verify_microsoft_client_state.return_value = False

    notification_body = {
        "value": [
            {
                "subscriptionId": "sub123",
                "clientState": "invalid-state-999",
                "resource": "users/me/events",
            }
        ]
    }
    event = {
        "body": json.dumps(notification_body),
        "requestContext": {"requestId": "test-request-id"},
    }

    result = handler(event, None)

    assert result["statusCode"] == 401
    response_body = json.loads(result["body"])
    assert "invalid clientstate" in response_body["error"].lower()


def test_delta_sync_and_kcnf_upsert(mock_env, mock_dependencies):
    """Test delta sync processing and KCNF upsert."""
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.return_value = {
        "user_id": "user123",
        "provider": "microsoft",
        "client_state": "valid-state-456",
    }
    mock_dependencies["sync_repo"].verify_microsoft_client_state.return_value = True
    mock_dependencies["sync_repo"].get_sync_state.return_value = MagicMock(
        delta_link="old-delta-link"
    )

    # Mock delta sync response
    raw_event = {
        "id": "event123",
        "subject": "Test Meeting",
        "start": {"dateTime": "2025-01-10T14:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2025-01-10T15:00:00Z", "timeZone": "UTC"},
        "changeKey": "change123",
    }
    mock_dependencies["graph_client"].delta_sync.return_value = (
        [raw_event],
        "new-delta-link",
    )

    # Mock normalizer
    normalized_event = KairosCalendarEvent(
        pk="USER#user123#CAL#microsoft#event123",
        sk="EVENT",
        user_id="user123",
        provider="microsoft",
        provider_event_id="event123",
        title="Test Meeting",
        start=datetime(2025, 1, 10, 14, 0, tzinfo=UTC),
        end=datetime(2025, 1, 10, 15, 0, tzinfo=UTC),
        all_day=False,
        timezone="UTC",
        provider_version="change123",
        ingested_at=datetime.now(UTC).isoformat(),
        ttl=int((datetime(2025, 1, 10, 15, 0, tzinfo=UTC) + timedelta(days=30)).timestamp()),
    )

    with patch(
        "src.handlers.outlook_calendar_webhook.normalize_microsoft_event",
        return_value=normalized_event,
    ):
        notification_body = {
            "value": [
                {
                    "subscriptionId": "sub123",
                    "clientState": "valid-state-456",
                    "resource": "users/me/events",
                }
            ]
        }
        event = {
            "body": json.dumps(notification_body),
            "requestContext": {"requestId": "test-request-id"},
        }

        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_dependencies["graph_client"].delta_sync.assert_called_once_with(
            "user123", "old-delta-link"
        )
        mock_dependencies["events_repo"].upsert.assert_called_once()
        mock_dependencies["sync_repo"].update_delta_link.assert_called_once_with(
            "user123", "microsoft", "new-delta-link"
        )


def test_410_gone_handling(mock_env, mock_dependencies):
    """Test 410 Gone handling: fallback to full sync and re-establish delta_link."""
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.return_value = {
        "user_id": "user123",
        "provider": "microsoft",
        "client_state": "valid-state-456",
    }
    mock_dependencies["sync_repo"].verify_microsoft_client_state.return_value = True
    mock_dependencies["sync_repo"].get_sync_state.return_value = MagicMock(
        delta_link="stale-delta-link"
    )

    # Mock 410 Gone response
    mock_dependencies["graph_client"].delta_sync.side_effect = Exception("410")

    # Mock full sync fallback
    raw_event = {
        "id": "event456",
        "subject": "Full Sync Event",
        "start": {"dateTime": "2025-01-11T10:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2025-01-11T11:00:00Z", "timeZone": "UTC"},
        "changeKey": "change456",
    }
    mock_dependencies["graph_client"].list_events.return_value = (
        [raw_event],
        "new-delta-link-after-410",
    )

    normalized_event = KairosCalendarEvent(
        pk="USER#user123#CAL#microsoft#event456",
        sk="EVENT",
        user_id="user123",
        provider="microsoft",
        provider_event_id="event456",
        title="Full Sync Event",
        start=datetime(2025, 1, 11, 10, 0, tzinfo=UTC),
        end=datetime(2025, 1, 11, 11, 0, tzinfo=UTC),
        all_day=False,
        timezone="UTC",
        provider_version="change456",
        ingested_at=datetime.now(UTC).isoformat(),
        ttl=int((datetime(2025, 1, 11, 11, 0, tzinfo=UTC) + timedelta(days=30)).timestamp()),
    )

    with patch(
        "src.handlers.outlook_calendar_webhook.normalize_microsoft_event",
        return_value=normalized_event,
    ):
        notification_body = {
            "value": [
                {
                    "subscriptionId": "sub123",
                    "clientState": "valid-state-456",
                    "resource": "users/me/events",
                }
            ]
        }
        event = {
            "body": json.dumps(notification_body),
            "requestContext": {"requestId": "test-request-id"},
        }

        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_dependencies["graph_client"].list_events.assert_called_once_with("user123")
        mock_dependencies["events_repo"].upsert.assert_called_once()
        mock_dependencies["sync_repo"].update_delta_link.assert_called_once_with(
            "user123", "microsoft", "new-delta-link-after-410"
        )


def test_multiple_notifications_in_batch(mock_env, mock_dependencies):
    """Test handling multiple notifications in a single webhook payload.

    Microsoft can send multiple notifications in one request.
    """
    mock_dependencies["sync_repo"].get_by_microsoft_subscription_id.side_effect = [
        {"user_id": "user123", "provider": "microsoft", "client_state": "state1"},
        {"user_id": "user456", "provider": "microsoft", "client_state": "state2"},
    ]
    mock_dependencies["sync_repo"].verify_microsoft_client_state.return_value = True
    mock_dependencies["sync_repo"].get_sync_state.side_effect = [
        MagicMock(delta_link="delta1"),
        MagicMock(delta_link="delta2"),
    ]
    mock_dependencies["graph_client"].delta_sync.side_effect = [
        ([], "new-delta1"),
        ([], "new-delta2"),
    ]

    notification_body = {
        "value": [
            {
                "subscriptionId": "sub123",
                "clientState": "state1",
                "resource": "users/user123/events",
            },
            {
                "subscriptionId": "sub456",
                "clientState": "state2",
                "resource": "users/user456/events",
            },
        ]
    }
    event = {
        "body": json.dumps(notification_body),
        "requestContext": {"requestId": "test-request-id"},
    }

    result = handler(event, None)

    assert result["statusCode"] == 200
    assert mock_dependencies["graph_client"].delta_sync.call_count == 2


def test_malformed_body(mock_env, mock_dependencies):
    """Test handling of malformed JSON body."""
    event = {
        "body": "not-valid-json{{{",
        "requestContext": {"requestId": "test-request-id"},
    }

    result = handler(event, None)

    assert result["statusCode"] == 400
    response_body = json.loads(result["body"])
    assert "invalid json" in response_body["error"].lower()


def test_missing_body(mock_env, mock_dependencies):
    """Test handling of missing body."""
    event = {"requestContext": {"requestId": "test-request-id"}}

    result = handler(event, None)

    assert result["statusCode"] == 400
    response_body = json.loads(result["body"])
    assert "missing body" in response_body["error"].lower()

