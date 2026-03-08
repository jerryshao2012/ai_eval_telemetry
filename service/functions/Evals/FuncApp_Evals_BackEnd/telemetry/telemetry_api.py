import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

import azure.functions as func

telemetry_bp = func.Blueprint()
MAX_EVENTS_PER_REQUEST = 500


def _json_response(payload: Dict[str, Any], status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype="application/json"
    )


def _extract_events(req_body: Any) -> List[Dict[str, Any]]:
    if isinstance(req_body, list):
        events = req_body
    elif isinstance(req_body, dict) and "events" in req_body:
        events = req_body["events"]
    elif isinstance(req_body, dict):
        events = [req_body]
    else:
        raise ValueError("Unsupported telemetry payload format.")

    if not isinstance(events, list) or not events:
        raise ValueError("No events provided in payload.")

    if len(events) > MAX_EVENTS_PER_REQUEST:
        raise ValueError(
            f"Too many events provided in payload. Maximum supported batch size is {MAX_EVENTS_PER_REQUEST}."
        )

    normalized_events: List[Dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"Telemetry event at index {index} must be a JSON object.")
        normalized_events.append(event)

    return normalized_events


def _enrich_event(event: Dict[str, Any], trace_id: str, default_received_at: str) -> Dict[str, Any]:
    enriched_event = dict(event)
    enriched_event.setdefault("id", str(uuid4()))
    enriched_event.setdefault("ingestionId", trace_id)
    enriched_event.setdefault("receivedAt", default_received_at)
    return enriched_event


@telemetry_bp.route(route="telemetry", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@telemetry_bp.event_hub_output(
    arg_name="event",
    event_hub_name="ai-eval-telemetry",
    connection="EventHubConnection"
)
@telemetry_bp.cosmos_db_output(
    arg_name="cosmos",
    database_name="TelemetryDB",
    container_name="Events",
    connection="CosmosDBConnection",
    create_if_not_exists=True
)
def ingest_telemetry(req: func.HttpRequest, event: func.Out[str], cosmos: func.Out[func.Document]) -> func.HttpResponse:
    """
    HTTP Trigger function to receive telemetry events and push them to Event Hubs or Cosmos DB.
    """
    trace_id = (
            req.headers.get("x-request-id")
            or req.headers.get("x-ms-client-request-id")
            or str(uuid4())
    )
    target = (req.params.get("target") or "eventhub").lower()
    default_received_at = datetime.now(timezone.utc).isoformat()

    logging.info("Processing telemetry ingestion request trace_id=%s target=%s", trace_id, target)

    try:
        req_body = req.get_json()
    except ValueError:
        logging.warning("Rejected request with invalid JSON trace_id=%s", trace_id)
        return _json_response({"error": "Invalid JSON payload", "ingestionId": trace_id}, 400)

    try:
        events = _extract_events(req_body)
    except ValueError as exc:
        logging.warning("Rejected telemetry payload trace_id=%s reason=%s", trace_id, exc)
        return _json_response({"error": str(exc), "ingestionId": trace_id}, 400)

    enriched_events = [_enrich_event(evt, trace_id, default_received_at) for evt in events]
    logging.info(
        "Validated telemetry payload trace_id=%s event_count=%d target=%s",
        trace_id,
        len(enriched_events),
        target,
    )

    if target == "cosmosdb":
        cosmos_docs = func.DocumentList([func.Document.from_dict(e) for e in enriched_events])
        for doc in cosmos_docs:
            cosmos.set(doc)
        logging.info("Queued telemetry batch for Cosmos DB trace_id=%s event_count=%d", trace_id, len(enriched_events))
    else:
        formatted_events = [json.dumps(e) for e in enriched_events]
        for formatted_event in formatted_events:
            event.set(formatted_event)
        logging.info("Queued telemetry batch for Event Hub trace_id=%s event_count=%d", trace_id, len(enriched_events))

    return _json_response(
        {
            "status": "accepted",
            "emitted": len(enriched_events),
            "target": target,
            "trace_id": trace_id,
        },
        202,
    )


@telemetry_bp.event_hub_message_trigger(
    arg_name="azeventhub",
    event_hub_name="ai-eval-telemetry",
    connection="EventHubConnection",
    cardinality="many"
)
@telemetry_bp.cosmos_db_output(
    arg_name="cosmos",
    database_name="TelemetryDB",
    container_name="Events",
    connection="CosmosDBConnection",
    create_if_not_exists=True
)
def eventhub_to_cosmos(azeventhub: List[func.EventHubEvent], cosmos: func.Out[func.Document]):
    """
    Event Hub trigger function to save telemetry events to Cosmos DB.
    """
    events_to_save = []
    dropped_events = 0

    for event in azeventhub:
        try:
            body = event.get_body().decode("utf-8")
            event_data = json.loads(body)
            if not isinstance(event_data, dict):
                raise ValueError("Event Hub payload must decode to a JSON object.")

            event_data.setdefault("id", str(uuid4()))
            event_data.setdefault("receivedAt", datetime.now(timezone.utc).isoformat())
            events_to_save.append(func.Document.from_dict(event_data))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            dropped_events += 1
            logging.error("Dropped malformed Event Hub message reason=%s", exc)
        except Exception as exc:
            dropped_events += 1
            logging.exception("Unexpected error while processing Event Hub message: %s", exc)

    if events_to_save:
        cosmos_docs = func.DocumentList(events_to_save)
        for doc in cosmos_docs:
            cosmos.set(doc)
        logging.info(
            "Saved telemetry batch from Event Hub to Cosmos DB saved_count=%d dropped_count=%d",
            len(events_to_save),
            dropped_events,
        )
    elif dropped_events:
        logging.warning("No Event Hub messages were persisted dropped_count=%d", dropped_events)
