import base64
import gzip
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from elevator_monitor.edge_sync import (
    CloudIngestClient,
    EdgeSyncQueue,
    build_alert_payload,
    build_context_payload,
    build_event_id,
    build_heartbeat_payload,
)


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class TestEdgeSync(unittest.TestCase):
    def test_build_event_payloads(self):
        heartbeat = build_heartbeat_payload(
            elevator_id="elevator-1",
            device_id="edge-1",
            site_id="site-a",
            site_name="Tower A",
            health_payload={"updated_at_ms": 1234, "status": "running"},
        )
        self.assertEqual(heartbeat["elevator_id"], "elevator-1")
        self.assertEqual(heartbeat["health_payload"]["status"], "running")

        alert = build_alert_payload(
            elevator_id="elevator-1",
            device_id="edge-1",
            site_id="site-a",
            site_name="Tower A",
            alert_payload={"ts_ms": 1234, "level": "warning", "fault_type": "rope_looseness", "fault_confidence": 0.88},
            health_payload={"status": "running"},
        )
        self.assertEqual(alert["event_id"], build_event_id("elevator-1", 1234, "rope_looseness", "warning"))
        self.assertEqual(alert["fault_type"], "rope_looseness")

    def test_build_context_payload_compresses_csv(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "context.csv"
            csv_path.write_text("ts_ms,Ax\n1,0.1\n2,0.2\n", encoding="utf-8")
            payload = build_context_payload(
                event_id="evt-1",
                elevator_id="elevator-1",
                device_id="edge-1",
                site_id="site-a",
                site_name="Tower A",
                ts_ms=1234,
                csv_path=str(csv_path),
                max_raw_bytes=1024,
            )
            decoded = gzip.decompress(base64.b64decode(payload["content_b64"].encode("ascii"))).decode("utf-8")
            self.assertIn("ts_ms,Ax", decoded)
            self.assertEqual(payload["compression"], "gzip")

    def test_queue_dedup_and_drain(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            queue = EdgeSyncQueue(str(Path(tmp_dir) / "queue.sqlite3"))
            payload = {"elevator_id": "elevator-1"}
            self.assertTrue(queue.enqueue(delivery_id="heartbeat:1", endpoint="/api/v1/ingest/heartbeat", body=payload))
            self.assertFalse(queue.enqueue(delivery_id="heartbeat:1", endpoint="/api/v1/ingest/heartbeat", body=payload))
            client = CloudIngestClient(base_url="http://localhost:8085", api_token="k")
            with patch("urllib.request.urlopen", return_value=_FakeResponse({"ok": True})):
                result = queue.drain(client=client, limit=4)
            self.assertEqual(result["sent"], 1)
            self.assertEqual(queue.count(), 0)

    def test_queue_retry_on_http_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            queue = EdgeSyncQueue(str(Path(tmp_dir) / "queue.sqlite3"))
            queue.enqueue(delivery_id="alert:1", endpoint="/api/v1/ingest/alert", body={"event_id": "evt-1"})
            client = CloudIngestClient(base_url="http://localhost:8085", api_token="k")
            body = io.BytesIO(b'{"error":"boom"}')
            error = urllib.error.HTTPError(url="http://localhost", code=500, msg="boom", hdrs=None, fp=body)
            with patch("urllib.request.urlopen", side_effect=error):
                result = queue.drain(client=client, limit=4)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(queue.count(), 1)


if __name__ == "__main__":
    unittest.main()
