#!/usr/bin/env python3
import importlib
import json
import sys
import types
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]


def install_azure_functions_stub() -> None:
    if "azure.functions" in sys.modules:
        return

    azure_module = types.ModuleType("azure")
    functions_module = types.ModuleType("azure.functions")

    class AuthLevel:
        FUNCTION = "FUNCTION"

    class Blueprint:
        def route(self, *args, **kwargs):
            return self._identity_decorator

        def event_hub_output(self, *args, **kwargs):
            return self._identity_decorator

        def cosmos_db_output(self, *args, **kwargs):
            return self._identity_decorator

        def event_hub_message_trigger(self, *args, **kwargs):
            return self._identity_decorator

        @staticmethod
        def _identity_decorator(fn):
            return fn

    class HttpResponse:
        def __init__(self, body, status_code=200, mimetype=None):
            self._body = body.encode("utf-8") if isinstance(body, str) else body
            self.status_code = status_code
            self.mimetype = mimetype

        def get_body(self):
            return self._body

    class HttpRequest:
        def __init__(self, body=None, headers=None, params=None, raise_on_get_json=False):
            self._body = body
            self.headers = headers or {}
            self.params = params or {}
            self._raise_on_get_json = raise_on_get_json

        def get_json(self):
            if self._raise_on_get_json:
                raise ValueError("Invalid JSON payload")
            return self._body

    class Document(dict):
        @classmethod
        def from_dict(cls, value):
            return cls(value)

    class DocumentList(list):
        pass

    class EventHubEvent:
        def __init__(self, body):
            self._body = body

        def get_body(self):
            return self._body

    class Out:
        def __init__(self):
            self.value = None

        def set(self, value):
            self.value = value

        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    functions_module.AuthLevel = AuthLevel
    functions_module.Blueprint = Blueprint
    functions_module.Document = Document
    functions_module.DocumentList = DocumentList
    functions_module.EventHubEvent = EventHubEvent
    functions_module.HttpRequest = HttpRequest
    functions_module.HttpResponse = HttpResponse
    functions_module.Out = Out

    azure_module.functions = functions_module
    sys.modules["azure"] = azure_module
    sys.modules["azure.functions"] = functions_module


install_azure_functions_stub()
sys.path.insert(0, str(APP_ROOT))

telemetry_api = importlib.import_module("telemetry.telemetry_api")
func = importlib.import_module("azure.functions")


class TelemetryFunctionTests(unittest.TestCase):
    def parse_response(self, response):
        return json.loads(response.get_body().decode("utf-8"))

    def test_ingest_routes_to_eventhub_by_default(self):
        req = func.HttpRequest(
            body={"eventType": "eval.completed"},
            headers={"x-request-id": "req-123"},
        )
        event_out = func.Out()
        cosmos_out = func.Out()

        response = telemetry_api.ingest_telemetry(req, event_out, cosmos_out)
        payload = self.parse_response(response)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(payload["target"], "eventhub")
        self.assertEqual(payload["ingestionId"], "req-123")
        self.assertEqual(len(event_out.value), 1)
        emitted = json.loads(event_out.value[0])
        self.assertEqual(emitted["eventType"], "eval.completed")
        self.assertEqual(emitted["ingestionId"], "req-123")
        self.assertIn("id", emitted)
        self.assertIn("receivedAt", emitted)
        self.assertIsNone(cosmos_out.value)

    def test_ingest_routes_directly_to_cosmosdb(self):
        req = func.HttpRequest(
            body={"events": [{"eventType": "eval.started"}, {"eventType": "eval.completed"}]},
            params={"target": "cosmosdb"},
        )
        event_out = func.Out()
        cosmos_out = func.Out()

        response = telemetry_api.ingest_telemetry(req, event_out, cosmos_out)
        payload = self.parse_response(response)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(payload["target"], "cosmosdb")
        self.assertIsNone(event_out.value)
        self.assertEqual(len(cosmos_out.value), 2)
        self.assertEqual(cosmos_out.value[0]["eventType"], "eval.started")
        self.assertIn("id", cosmos_out.value[0])

    def test_ingest_rejects_invalid_json(self):
        req = func.HttpRequest(raise_on_get_json=True)

        response = telemetry_api.ingest_telemetry(req, func.Out(), func.Out())
        payload = self.parse_response(response)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"], "Invalid JSON payload")
        self.assertIn("ingestionId", payload)

    def test_ingest_rejects_non_object_event(self):
        req = func.HttpRequest(body={"events": [{"eventType": "ok"}, "bad"]})

        response = telemetry_api.ingest_telemetry(req, func.Out(), func.Out())
        payload = self.parse_response(response)

        self.assertEqual(response.status_code, 400)
        self.assertIn("Telemetry event at index 1", payload["error"])

    def test_ingest_rejects_oversized_batch(self):
        req = func.HttpRequest(body=[{"eventType": "bulk"}] * 501)

        response = telemetry_api.ingest_telemetry(req, func.Out(), func.Out())
        payload = self.parse_response(response)

        self.assertEqual(response.status_code, 400)
        self.assertIn("Maximum supported batch size is 500", payload["error"])

    def test_eventhub_to_cosmos_persists_only_valid_messages(self):
        events = [
            func.EventHubEvent(json.dumps({"eventType": "eval.completed"}).encode("utf-8")),
            func.EventHubEvent(b'{"bad_json"'),
            func.EventHubEvent(json.dumps(["not-an-object"]).encode("utf-8")),
        ]
        cosmos_out = func.Out()

        telemetry_api.eventhub_to_cosmos(events, cosmos_out)

        self.assertIsNotNone(cosmos_out.value)
        self.assertEqual(len(cosmos_out.value), 1)
        self.assertEqual(cosmos_out.value[0]["eventType"], "eval.completed")
        self.assertIn("id", cosmos_out.value[0])
        self.assertIn("receivedAt", cosmos_out.value[0])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TelemetryFunctionTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
