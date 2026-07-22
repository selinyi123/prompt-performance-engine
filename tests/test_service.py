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


def transport(prompt: str) -> str:
    return json.dumps({"optimized_prompt": prompt})


MODEL_RESPONSE = transport("Produce a complete and directly usable report.")


class ServiceTests(unittest.TestCase):
    def make_service(self, root, responses=None):
        sequence = list(responses or [MODEL_RESPONSE])
        return OptimizationService(
            store=JobStore(root / "jobs.sqlite3"),
            artifacts=ArtifactStore(root / "artifacts"),
            adapter_factory=lambda: MockSequenceAdapter(sequence),
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
                    "schema_version": "2.0.0",
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
                "schema_version": "2.0.0",
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
            service = self.make_service(
                root,
                [
                    transport("Candidate one."),
                    transport("Candidate two."),
                    transport("Candidate three."),
                    '{"selected_index": 2}',
                ],
            )
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
                        "schema_version": "2.0.0",
                        "source_prompt": "Write a report.",
                        "mode": "maximum_quality",
                        "output_format": "standard",
                        "candidate_count": 3,
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
                self.assertEqual(
                    artifact["runtime"]["selection"]["candidate_count"],
                    3,
                )
                self.assertEqual(
                    artifact["runtime"]["selection"]["selected_index"],
                    2,
                )
            finally:
                server.shutdown()
                server.server_close()
                service.stop()
                thread.join(1)

    def test_service_rejects_invalid_candidate_count(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            try:
                with self.assertRaisesRegex(ValueError, "candidate_count"):
                    service.submit(
                        {
                            "schema_version": "2.0.0",
                            "source_prompt": "Write a report.",
                            "mode": "maximum_quality",
                            "output_format": "standard",
                            "candidate_count": 0,
                        },
                        idempotency_key="invalid-count",
                    )
            finally:
                service.stop()

    def test_service_rejects_unknown_missing_and_mistyped_fields_before_queueing(self):
        valid = {
            "schema_version": "2.0.0",
            "source_prompt": "Write a report.",
            "mode": "maximum_quality",
            "output_format": "standard",
        }
        invalid_requests = {
            "unknown": {**valid, "ignored": True},
            "missing": {key: value for key, value in valid.items() if key != "mode"},
            "scalar": {**valid, "source_prompt": ["not", "text"]},
            "pseudo_array": {**valid, "required_behaviors": "preserve citations"},
            "array_item": {**valid, "forbidden_changes": [7]},
            "candidate_count": {**valid, "candidate_count": "3"},
        }
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            try:
                for name, request in invalid_requests.items():
                    with self.subTest(name=name), self.assertRaises(
                        (TypeError, ValueError)
                    ):
                        service.submit(request, idempotency_key=f"invalid-{name}")
                self.assertEqual(service.metrics.snapshot().get("submitted", 0), 0)
            finally:
                service.stop()

    def test_http_rejects_contract_errors_before_creating_a_job(self):
        valid = {
            "schema_version": "2.0.0",
            "source_prompt": "Write a report.",
            "mode": "maximum_quality",
            "output_format": "standard",
        }
        invalid_requests = (
            {**valid, "unknown": "discarded before"},
            {key: value for key, value in valid.items() if key != "output_format"},
            {**valid, "required_behaviors": "not-an-array"},
            {**valid, "candidate_count": False},
        )
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            server = create_http_server(service, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/optimize"
            try:
                for index, request_data in enumerate(invalid_requests):
                    with self.subTest(index=index):
                        request = urllib.request.Request(
                            url,
                            data=json.dumps(request_data).encode("utf-8"),
                            method="POST",
                            headers={
                                "Content-Type": "application/json",
                                "Idempotency-Key": f"invalid-http-{index}",
                            },
                        )
                        with self.assertRaises(urllib.error.HTTPError) as context:
                            urllib.request.urlopen(request, timeout=1)
                        self.assertEqual(context.exception.code, 400)
                        error = json.loads(context.exception.read())
                        self.assertTrue(error["error"])
                self.assertEqual(service.metrics.snapshot().get("submitted", 0), 0)
            finally:
                server.shutdown()
                server.server_close()
                service.stop()
                thread.join(1)

    def test_http_rejects_duplicate_json_fields_before_creating_a_job(self):
        body = (
            '{"schema_version":"2.0.0","source_prompt":"Write a report.",'
            '"mode":"balanced","mode":"concise",'
            '"output_format":"standard"}'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            server = create_http_server(service, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/optimize"
            try:
                request = urllib.request.Request(
                    url,
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request, timeout=1)
                self.assertEqual(context.exception.code, 400)
                error = json.loads(context.exception.read())
                self.assertIn("duplicate field", error["error"])
                self.assertEqual(service.metrics.snapshot().get("submitted", 0), 0)
            finally:
                server.shutdown()
                server.server_close()
                service.stop()
                thread.join(1)

    def test_http_rejects_high_precision_near_integer_candidate_count(self):
        body = (
            '{"schema_version":"2.0.0","source_prompt":"Write a report.",'
            '"mode":"balanced","output_format":"standard",'
            '"candidate_count":1.0000000000000000000000000001}'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            server = create_http_server(service, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/optimize"
            try:
                request = urllib.request.Request(
                    url,
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request, timeout=1)
                self.assertEqual(context.exception.code, 400)
                self.assertEqual(service.metrics.snapshot().get("submitted", 0), 0)
            finally:
                server.shutdown()
                server.server_close()
                service.stop()
                thread.join(1)

    def test_http_rejects_excessive_json_nesting_with_400(self):
        body = (
            '{"value":' + "[" * 10_000 + "0" + "]" * 10_000 + "}"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            server = create_http_server(service, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/optimize"
            try:
                request = urllib.request.Request(
                    url,
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request, timeout=1)
                self.assertEqual(context.exception.code, 400)
                error = json.loads(context.exception.read())
                self.assertIn("must be valid JSON", error["error"])
                self.assertEqual(service.metrics.snapshot().get("submitted", 0), 0)
            finally:
                server.shutdown()
                server.server_close()
                service.stop()
                thread.join(1)


if __name__ == "__main__":
    unittest.main()
