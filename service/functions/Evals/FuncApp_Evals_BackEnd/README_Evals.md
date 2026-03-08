# AI Eval Telemetry Backend Implementations

This directory (`FuncApp_Evals_BackEnd`) implements handling the telemetry events pipeline using an Azure Python Function App.

## Application Structure (Blueprints)
The function app is modularized using the Azure Functions Blueprints pattern. 
- `function_app.py`: Acts as the main entry point, simply initializing `func.FunctionApp()` and registering the blueprint via `app.register_functions(...)`.
- `telemetry_api.py`: Implements the `telemetry_bp` blueprint, containing all routing and trigger functionality for capturing telemetry.

## Telemetry Functions

1. **`ingest_telemetry` (HTTP Trigger)**
   - **Endpoint**: `POST /api/telemetry`
   - **Description**: Receives incoming telemetry payload (single event or an array of events) in JSON format.
   - **Routing logic**:
     - If the URL query parameter `target` is set to `cosmosdb` (e.g., `?target=cosmosdb`), the function skips the Event Hub and saves the events directly into the `TelemetryDB` database inside Cosmos DB.
     - Otherwise, it natively routes the events into the configured Event Hub (`ai-eval-telemetry`).
   - The function now enforces a tighter contract:
     - accepts either a single JSON object, an array of JSON objects, or `{ "events": [...] }`
     - rejects empty payloads, non-object events, and batches larger than 500 events
     - enriches each accepted event with `id`, `tracId`, and `receivedAt` if they are not already present
   - The HTTP response includes `tracId` so downstream Event Hub and Cosmos activity can be correlated with the original request.

2. **`eventhub_to_cosmos` (Event Hub Trigger)**
   - **Description**: Acts as an asynchronous consumer for the `ai-eval-telemetry` Event Hub. 
   - **Behavior**: Upon triggered by new telemetry events within the Event Hub, it automatically ingests and logs the events securely into Cosmos DB, acting as the finalized storage layer for analytics.
   - Decodes utf-8 events, rejects malformed or non-object payloads, and logs both persisted and dropped message counts.

## Review Findings And Implemented Improvements

The initial implementation was structurally sound, but there were a few operational gaps that were worth correcting before treating it as production-ready:

1. **Payload validation was too weak**
   - The function accepted arrays containing non-object items and only checked whether the payload existed.
   - This could fail later during Cosmos conversion or create inconsistent downstream documents.
   - Implemented: strict extraction and validation of event batches, plus a hard cap of 500 events per request.

2. **Application Insights request telemetry was disabled**
   - `host.json` excluded the `Request` telemetry type from sampling.
   - That removes basic visibility into request rate, failures, duration, and dependency correlation for the ingestion endpoint itself.
   - Implemented: request telemetry is now allowed through, and log levels were made explicit.

3. **No stable correlation metadata was added to events**
   - The original code returned `202 Accepted` but did not stamp events with a request-level correlation identifier or guaranteed document identifier.
   - Implemented: events now default `id`, `tracId`, and `receivedAt` so requests, Event Hub messages, and Cosmos documents can be tied together.

4. **Operational logging was too sparse**
   - The original logs were mostly debug-only and did not capture dropped Event Hub messages or accepted batch identity.
   - Implemented: ingestion and persistence now emit structured informational logs with counts and correlation identifiers.

5. **The deployment package contained unnecessary dependencies**
   - `numpy`, `pandas`, `requests`, and several Azure SDK packages were listed but unused by this Function App.
   - Implemented: `requirements.txt` was reduced to the single package currently required by the code path, which should reduce cold-start overhead and deployment size.

## Remaining Recommendations

The following improvements were identified during review but were not implemented in this pass because they require either schema decisions or infrastructure coordination:

1. **Define a real telemetry schema**
   - The current validation only enforces shape and metadata, not business fields such as event type, timestamp source, tenant, or model identifiers.
   - A shared schema would prevent bad producer data from reaching Event Hub or Cosmos.

2. **Model Cosmos partitioning explicitly**
   - The code writes documents with an `id`, but the container partition key strategy is not captured in this app.
   - That should be aligned with the expected query patterns before production load.

3. **Add automated tests**
   - There are no unit tests covering payload parsing, direct-to-Cosmos routing, or malformed Event Hub messages.
   - Tests would be the next step before wider rollout.

## Quick Verification

Run the local command-line verification script:

```bash
python tests/test_telemetry_cli.py
```

This script verifies:
- HTTP ingestion to Event Hub
- direct HTTP routing to Cosmos DB
- invalid JSON rejection
- non-object event rejection
- oversized batch rejection
- Event Hub to Cosmos persistence with malformed-message filtering
