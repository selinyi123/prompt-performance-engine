import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from prompt_performance_engine.adapters import MockSequenceAdapter
from prompt_performance_engine.service import (
    ArtifactStore,
    IdempotencyConflict,
    JobStore,
    OptimizationService,
    create_http_server,
)
from prompt_performance_engine.validation import validate_artifact


MODEL_RESPONSE = (
    "## Optimized Prompt\n\n"
    "```text\nProduce a complete and directly usable report.\n```"
)


class ServiceTests(unittest.TestCase):
    def make_service(self, root):
        return OptimizationService(
            store=JobStore(root / "jobs.sqlite3"),
            artifacts=ArtifactStore(root / "artifacts"),
            adapter_factory=lambda: MockSequenceAdapter([MODEL_RESPONSE]),
        )

    def wait_for(self, service, job_id, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = service.get_job(job_id)
            if job and job["status"] in {"succeeded", "failed"}:
                return job
            time.sleep(0.01)
        self.fail("Job did not finish before timeout.")

    def test_persistent_job_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            try:
                request = {
                    "schema_version": "1.0.0",
                    "source_prompt": "Write a report.",
                    "mode": "maximum_quality",
                    "output_format": "standard",
                }
                first = service.submit(request, idempotency_key="same-request")
                replay = service.submit(request, idempotency_key="same-request")
                self.assertEqual(first["job_id"], replay["job_id"])
                with self.assertRaises(IdempotencyConflict):
                    service.submit(
                        {**request, "source_prompt": "Different prompt."},
                        idempotency_key="same-request",
                    )
                finished = self.wait_for(service, first["job_id"])
                self.assertEqual(finished["status"], "succeeded")
                artifact = service.get_artifact(first["job_id"])
                self.assertIsNotNone(artifact)
                self.assertEqual(validate_artifact(artifact), [])
                self.assertEqual(service.metrics.snapshot()["submitted"], 1)
                self.assertEqual(service.metrics.snapshot()["idempotent_replays"], 1)
            finally:
                service.stop()

    def test_interrupted_job_is_recovered_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            request = {
                "schema_version": "1.0.0",
                "source_prompt": "Write a report.",
                "mode": "maximum_quality",
                "output_format": "standard",
                "domain": None,
                "audience": None,
                "target_model": None,
                "target_surface": "api",
                "required_behaviors": [],
                "forbidden_changes": [],
            }
            job, _ = store.create_or_get(request, "recover-me")
            self.assertTrue(store.mark_running(job["job_id"]))
            service = OptimizationService(
                store=store,
                artifacts=ArtifactStore(root / "artifacts"),
                adapter_factory=lambda: MockSequenceAdapter([MODEL_RESPONSE]),
            )
            try:
                finished = self.wait_for(service, job["job_id"])
                self.assertEqual(finished["status"], "succeeded")
                self.assertEqual(service.metrics.snapshot()["recovered"], 1)
            finally:
                service.stop()

    def test_http_auth_health_and_job_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            server = create_http_server(
                service,
                host="127.0.0.1",
                port=0,
                service_token="service-secret",
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=1) as response:
                    health = json.loads(response.read())
                self.assertEqual(health["status"], "ok")

                request_body = json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "source_prompt": "Write a report.",
                        "mode": "maximum_quality",
                        "output_format": "standard",
                    }
                ).encode()
                unauthorized = urllib.request.Request(
                    f"{base}/v1/optimize",
                    data=request_body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": "http-job",
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(unauthorized, timeout=1)
                self.assertEqual(context.exception.code, 401)

                authorized = urllib.request.Request(
                    f"{base}/v1/optimize",
                    data=request_body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": "http-job",
                        "Authorization": "Bearer service-secret",
                    },
                )
                with urllib.request.urlopen(authorized, timeout=1) as response:
                    submitted = json.loads(response.read())
                finished = self.wait_for(service, submitted["job_id"])
                self.assertEqual(finished["status"], "succeeded")

                artifact_request = urllib.request.Request(
                    f"{base}/v1/artifacts/{submitted['job_id']}",
                    headers={"Authorization": "Bearer service-secret"},
                )
                with urllib.request.urlopen(artifact_request, timeout=1) as response:
                    artifact = json.loads(response.read())
                self.assertEqual(validate_artifact(artifact), [])
            finally:
                server.shutdown()
                server.server_close()
                service.stop()
                thread.join(1)


if __name__ == "__main__":
    unittest.main()
