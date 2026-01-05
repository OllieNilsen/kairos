"""Lambda handler for Microsoft Graph (Outlook) Calendar webhook notifications."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aws_lambda_powertools import Logger

# Support both Lambda (adapters...) and test (src.adapters...) import paths
try:
    from adapters.calendar_events_repo import CalendarEventsRepository
    from adapters.calendar_normalizer import normalize_microsoft_event
    from adapters.calendar_sync_state_repo import CalendarSyncStateRepository
    from adapters.microsoft_graph import MicrosoftGraphClient
except ImportError:
    from src.adapters.calendar_events_repo import CalendarEventsRepository
    from src.adapters.calendar_normalizer import normalize_microsoft_event
    from src.adapters.calendar_sync_state_repo import CalendarSyncStateRepository
    from src.adapters.microsoft_graph import MicrosoftGraphClient

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="kairos-outlook-calendar-webhook")

# Lazy initialization
_calendar_events_repo: CalendarEventsRepository | None = None
_calendar_sync_state_repo: CalendarSyncStateRepository | None = None
_microsoft_graph_client: MicrosoftGraphClient | None = None


def get_calendar_events_repo() -> CalendarEventsRepository:
    """Get or create the calendar events repository."""
    global _calendar_events_repo
    if _calendar_events_repo is None:
        table_name = os.environ["CALENDAR_EVENTS_TABLE"]
        _calendar_events_repo = CalendarEventsRepository(table_name)
    return _calendar_events_repo


def get_calendar_sync_state_repo() -> CalendarSyncStateRepository:
    """Get or create the calendar sync state repository."""
    global _calendar_sync_state_repo
    if _calendar_sync_state_repo is None:
        table_name = os.environ["CALENDAR_SYNC_STATE_TABLE"]
        _calendar_sync_state_repo = CalendarSyncStateRepository(table_name)
    return _calendar_sync_state_repo


def get_microsoft_graph_client() -> MicrosoftGraphClient:
    """Get or create the Microsoft Graph client."""
    global _microsoft_graph_client
    if _microsoft_graph_client is None:
        _microsoft_graph_client = MicrosoftGraphClient.from_ssm()
    return _microsoft_graph_client


def handler(event: dict[str, Any], context: LambdaContext | None) -> dict[str, Any]:
    """Handle Microsoft Graph webhook notifications.

    Supports:
    1. Validation token handshake (initial subscription setup)
    2. Notification processing with clientState verification
    3. Delta sync and KCNF upsert
    4. 410 Gone handling (fallback to full sync)

    Args:
        event: API Gateway event with webhook notification
        context: Lambda context

    Returns:
        API Gateway response
    """
    request_id = event.get("requestContext", {}).get("requestId", "unknown")
    logger.info("outlook_calendar_webhook_invoked", request_id=request_id)

    # Step 1: Validation token handshake
    query_params = event.get("queryStringParameters") or {}
    validation_token = query_params.get("validationToken")
    if validation_token:
        logger.info("validation_token_handshake", validation_token=validation_token)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": validation_token,
        }

    # Step 2: Parse notification body
    body_str = event.get("body")
    if not body_str:
        logger.warning("missing_body")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing body"}),
        }

    try:
        body = json.loads(body_str)
    except json.JSONDecodeError as e:
        logger.warning("invalid_json", error=str(e))
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid JSON"}),
        }

    notifications = body.get("value", [])
    logger.info("notifications_received", count=len(notifications))

    # Step 3: Process each notification
    sync_state_repo = get_calendar_sync_state_repo()
    events_repo = get_calendar_events_repo()
    graph_client = get_microsoft_graph_client()

    processed_count = 0
    for notification in notifications:
        subscription_id = notification.get("subscriptionId")
        client_state = notification.get("clientState")

        # Step 3a: O(1) routing via subscription_id
        route_info = sync_state_repo.get_by_microsoft_subscription_id(subscription_id)
        if not route_info:
            logger.warning("unknown_subscription", subscription_id=subscription_id)
            return {
                "statusCode": 401,
                "body": json.dumps(
                    {"error": f"Unknown subscription: {subscription_id}"}
                ),
            }

        user_id = route_info["user_id"]
        logger.info(
            "subscription_routed", subscription_id=subscription_id, user_id=user_id
        )

        # Step 3b: ClientState verification (early rejection)
        if not sync_state_repo.verify_microsoft_client_state(
            subscription_id, client_state
        ):
            logger.warning(
                "invalid_client_state",
                subscription_id=subscription_id,
                user_id=user_id,
            )
            return {
                "statusCode": 401,
                "body": json.dumps({"error": "Invalid clientState"}),
            }

        # Step 3c: Delta sync
        sync_state = sync_state_repo.get_sync_state(user_id, "microsoft")
        delta_link = sync_state.delta_link if sync_state else None

        try:
            events, new_delta_link = graph_client.delta_sync(user_id, delta_link)
            logger.info(
                "delta_sync_success",
                user_id=user_id,
                event_count=len(events),
            )
        except Exception as e:
            # Step 3d: 410 Gone handling
            if "410" in str(e):
                logger.warning(
                    "delta_link_expired_410_gone",
                    user_id=user_id,
                    error=str(e),
                )
                # Fallback to full sync
                events, new_delta_link = graph_client.list_events(user_id)
                logger.info(
                    "full_sync_fallback",
                    user_id=user_id,
                    event_count=len(events),
                )
            else:
                logger.error(
                    "delta_sync_error",
                    user_id=user_id,
                    error=str(e),
                )
                raise

        # Step 3e: Normalize and upsert events to KCNF
        for raw_event in events:
            try:
                normalized_event = normalize_microsoft_event(user_id, raw_event)
                events_repo.upsert(normalized_event)
                logger.info(
                    "event_upserted",
                    user_id=user_id,
                    provider_event_id=normalized_event.provider_event_id,
                )
            except Exception as e:
                logger.error(
                    "event_normalization_error",
                    user_id=user_id,
                    raw_event_id=raw_event.get("id"),
                    error=str(e),
                )
                # Continue processing other events
                continue

        # Step 3f: Update delta_link
        sync_state_repo.update_delta_link(user_id, "microsoft", new_delta_link)
        logger.info(
            "delta_link_updated",
            user_id=user_id,
        )

        processed_count += 1

    logger.info("notifications_processed", count=processed_count)
    return {
        "statusCode": 200,
        "body": json.dumps({"processed": processed_count}),
    }

