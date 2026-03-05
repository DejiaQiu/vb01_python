import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from elevator_monitor.dify_client import DifyWorkflowClient


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


class TestDifyWorkflowClient(unittest.TestCase):
    def test_run_workflow_success(self):
        client = DifyWorkflowClient(base_url="http://localhost/v1", api_key="k")

        with patch("urllib.request.urlopen", return_value=_FakeResponse({"workflow_run_id": "run-1", "task_id": "task-1"})):
            result = client.run_workflow(inputs={"a": 1}, user="u1", response_mode="blocking")

        self.assertTrue(result.dispatched)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.workflow_run_id, "run-1")
        self.assertEqual(result.task_id, "task-1")

    def test_run_workflow_http_error(self):
        client = DifyWorkflowClient(base_url="http://localhost/v1", api_key="k")
        body = io.BytesIO(json.dumps({"message": "bad request"}).encode("utf-8"))
        error = urllib.error.HTTPError(url=client.endpoint, code=400, msg="bad", hdrs=None, fp=body)

        with patch("urllib.request.urlopen", side_effect=error):
            result = client.run_workflow(inputs={"a": 1}, user="u1", response_mode="blocking")

        self.assertFalse(result.dispatched)
        self.assertEqual(result.status, "http_error")
        self.assertEqual(result.http_status, 400)


if __name__ == "__main__":
    unittest.main()
