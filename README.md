# AI Eval Telemetry

This implementation of the telemetry API (POST /api/telemetry) using the **Azure Functions v2 programming model for Python**.

This approach is significantly simpler and more scalable than running a traditional web framework (like Flask/FastAPI) because it uses native Event Hub Output Bindings. Instead of managing your own background threads, asyncio event loops, and SDK clients (like in your current`api.py`), you simply return the JSON payload, and the Azure Functions host handles the batching, retry logic, and connection polling to drop it into Event Hubs.

1. function_app.py
This is your main Azure Function entry point.
2. local.settings.json (For Local Development)
When running this locally with func start, you'll define your Event Hub connection string here. In production, this matches the App Settings configured in Azure (or Key Vault references).
3. host.json
Azure Functions controls the batched output behavior automatically. You can tune the underlying Event Hub host extension in host.json to control how quickly it drains buffered events.

Why this approach?

The telemetry API (e.g., `POST /api/telemetry`) has one job: receive a JSON payload, validate it, and drop it into Azure Event Hubs as fast as possible.
1. **Instant, Elastic Scaling for Bursty Traffic**: Telemetry data is notoriously bursty. Azure Functions (especially on a Consumption or Premium plan) can instantly scale out to handle thousands of concurrent ingestion requests and scale down to zero when idle. AKS Horizontal Pod Autoscaling (HPA) is generally slower to react to sudden spikes.
   * **Concurrency and Backpressure**: You no longer need `queue_max_size` or explicit `enqueue_timeout_seconds` in your own code. The Azure Function host inherently drops HTTP 429 limits or scales horizontally if traffic spikes, ensuring your container doesn't experience `OutOfMemory` errors trying to buffer bursts locally.
   * **Speed**: The function executes incredibly fast. It simply parses the incoming payload, validates shape, delegates the payload to the `event: func.Out` parameter, and immediately returns a `202 Accepted`. The platform then async-flushes this to Event Hub in the background.
2. **Native Event Hubs Bindings**: Azure Functions has native Output Bindings for Event Hubs. You don't need to write connection pooling or complex retry logic; you simply return the telemetry JSON payload from the function, and the platform handles bulk-publishing it to Event Hubs highly efficiently.
3. **Cost Efficiency**: Because the ingestion endpoint does minimal compute (just parsing a payload and pushing to a queue), the execution time will be in the low milliseconds. Serverless micro-billing is incredibly cost-effective for fast, I/O-bound operations.
