# AI Eval Telemetry Backend Implementations

This implementation of the telemetry API (POST /api/telemetry) using the **Azure Functions v2 programming model for Python**. This directory (`FuncApp_Evals_BackEnd`) implements handling the telemetry events pipeline using an Azure Python Function App.

This approach is significantly simpler and more scalable than running a traditional web framework (like Flask/FastAPI) because it uses native Event Hub Output Bindings. Instead of managing your own background threads, asyncio event loops, and SDK clients (like in your current`api.py`), you simply return the JSON payload, and the Azure Functions host handles the batching, retry logic, and connection polling to drop it into Event Hubs.

**Why this approach?**

The telemetry API (e.g., `POST /api/telemetry`) has one job: receive a JSON payload, validate it, and drop it into Azure Event Hubs as fast as possible.
1. **Instant, Elastic Scaling for Bursty Traffic**: Telemetry data is notoriously bursty. Azure Functions (especially on a Consumption or Premium plan) can instantly scale out to handle thousands of concurrent ingestion requests and scale down to zero when idle. AKS Horizontal Pod Autoscaling (HPA) is generally slower to react to sudden spikes.
   * **Concurrency and Backpressure**: You no longer need `queue_max_size` or explicit `enqueue_timeout_seconds` in your own code. The Azure Function host inherently drops HTTP 429 limits or scales horizontally if traffic spikes, ensuring your container doesn't experience `OutOfMemory` errors trying to buffer bursts locally.
   * **Speed**: The function executes incredibly fast. It simply parses the incoming payload, validates shape, delegates the payload to the `event: func.Out` parameter, and immediately returns a `202 Accepted`. The platform then async-flushes this to Event Hub in the background.
2. **Native Event Hubs Bindings**: Azure Functions has native Output Bindings for Event Hubs. You don't need to write connection pooling or complex retry logic; you simply return the telemetry JSON payload from the function, and the platform handles bulk-publishing it to Event Hubs highly efficiently.
3. **Cost Efficiency**: Because the ingestion endpoint does minimal compute (just parsing a payload and pushing to a queue), the execution time will be in the low milliseconds. Serverless micro-billing is incredibly cost-effective for fast, I/O-bound operations.

## Application Structure (Blueprints)
The function app is modularized using the Azure Functions Blueprints pattern. 
- `function_app.py`: Acts as the main entry point, simply initializing `func.FunctionApp()` and registering the blueprint via `app.register_functions(...)`.
- `telemetry_api.py`: Implements the `telemetry_bp` blueprint, containing all routing and trigger functionality for capturing telemetry.
- `local.settings.json` (For Local Development): When running this locally with func start, you'll define your Event Hub connection string here. In production, this matches the App Settings configured in Azure (or Key Vault references).
- `host.json`: Azure Functions controls the batched output behavior automatically. You can tune the underlying Event Hub host extension in host.json to control how quickly it drains buffered events.

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
     - enriches each accepted event with `id`, `traceId`, and `receivedAt` if they are not already present
   - The HTTP response includes `traceId` so downstream Event Hub and Cosmos activity can be correlated with the original request.

2. **`eventhub_to_cosmos` (Event Hub Trigger)**
   - **Description**: Acts as an asynchronous consumer for the `ai-eval-telemetry` Event Hub. 
   - **Behavior**: Upon triggered by new telemetry events within the Event Hub, it automatically ingests and logs the events securely into Cosmos DB, acting as the finalized storage layer for analytics.
   - **Batch Processing**: Configured with `cardinality="many"` to process events in batches rather than individually. This significantly improves throughput and reduces the number of calls to Cosmos DB.
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
   - Implemented: events now default `id`, `traceId`, and `receivedAt` so requests, Event Hub messages, and Cosmos documents can be tied together.

4. **Event Hub processing was inefficient (Single vs Batch)**
   - The original `eventhub_to_cosmos` function processed events one by one, which is inefficient for high-volume telemetry.
   - Implemented: Switched to batch processing by setting `cardinality="many"` in the trigger configuration. The function now receives a list of events (`List[func.EventHubEvent]`) and writes them to Cosmos DB in a single `DocumentList` operation. This aligns with the `maxBatchSize` setting in `host.json` and drastically improves throughput.

## Remaining Recommendations

The following improvements were identified during review but were not implemented in this pass because they require either schema decisions or infrastructure coordination:

1. **Define a real telemetry schema**
   - The current validation only enforces shape and metadata, not business fields such as event type, timestamp source, tenant, or model identifiers.
   - A shared schema would prevent bad producer data from reaching Event Hub or Cosmos.

2. **Model Cosmos partitioning explicitly**
   - The code writes documents with an `id`, but the container partition key strategy is not captured in this app.
   - That should be aligned with the expected query patterns before production load.

3. **Eliminate or drastically reduce cold starts**

   3.1. Upgrade to the Azure Functions Premium Plan (Recommended)

   The **Consumption Plan** is designed to scale down to zero when there's no traffic. When a new request arrives, Azure has to allocate a server, start the Python worker, and load your code—causing the 20-second delay.

   The **Elastic Premium Plan (EP)** provides dynamic scale but allows you to specify pre-warmed instances. 
   - **Always Ready Instances:** You can configure a minimum number of instances that are always running and ready to handle traffic instantly (0 cold start).
   - **Pre-warmed Instances:** As your traffic grows, Azure will warm up the next instance *before* routing traffic to it, preventing cold starts during scale-out events.

   *How to implement:* When provisioning your infrastructure (e.g., in your CDKTF `infra` codebase mentioned in your README), change the App Service Plan SKU from `Y1` (Consumption) to `EP1`, `EP2`, or `EP3` (Premium), and set the `minimum_elastic_instance_count`.

   3.2. Use a Dedicated App Service Plan

   If you have steady, predictable traffic, you can host the Function App on a **Dedicated App Service Plan** (Basic, Standard, Premium v2/v3).
   - Your functions run on dedicated VMs just like a standard web app.
     - Turn on the **"Always On"** setting in the Function App configuration. This prevents the idle timeout that stops the worker process.
     - **Pros:** Zero cold starts. Predictable billing.
     - **Cons:** You pay for the VMs 24/7 regardless of traffic. It does not scale out as aggressively as the Premium plan.

   3.3. Code & Configuration Optimizations (If stuck on Consumption)

   If you absolutely *must* stay on the Consumption plan, you can't eliminate cold starts entirely, but you can shave a few seconds off the 20-second duration:

   * **Avoid Global Scope Heavy Operations:** Ensure you aren't doing heavy lifting outside of your function handlers. Imports take time in Python. Lazy-load modules inside the function (`import my_heavy_module` inside [ingest_telemetry](telemetry/telemetry_api.py) if they aren't needed by every execution.
   * **Run from Package:** Enable `WEBSITE_RUN_FROM_PACKAGE=1` in your deployment settings. This mounts your deployment zip file directly as a read-only directory, dramatically improving I/O performance during startup compared to reading individual files scattered across the Azure file share.
   * **Worker Indexing:** You already have `"AzureWebJobsFeatureFlags": "EnableWorkerIndexing"` in your [local.settings.json](local.settings.json). Ensure this is set in production. It allows Azure to index your functions natively without spinning up the heavy Python language worker just to figure out what your functions are.

### Summary
If a 20-second cold start is a hard blocker for your business requirements, **upgrading to the Premium Plan with `Always Ready` instances is the only guaranteed solution** to practically eliminate it for an HTTP-triggered Telemetry API. Code optimizations on the Consumption plan might bring it down to 5-10 seconds, but never to zero.

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
