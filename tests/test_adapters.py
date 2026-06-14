import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from prompt_performance_engine.adapters import (
    AdapterCancelled,
    AdapterError,
    AdapterQuotaError,
    CancellationToken,
    CodexExecAdapter,
    ExternalCommandAdapter,
    OpenAIResponsesAdapter,
    RetryPolicy,
    ToolPermissionManifest,
)


class OpenAIResponsesAdapterTests(unittest.TestCase):
    def test_request_shape_and_usage_capture(self):
        observed = {}

        def sender(request, timeout):
            observed["url"] = request.full_url
            observed["timeout"] = timeout
            observed["authorization"] = request.headers["Authorization"]
            observed["body"] = json.loads(request.data.decode("utf-8"))
            return 200, json.dumps(
                {
                    "id": "resp_test",
                    "model": "test-model-2026-01-01",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "optimized",
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 4,
                        "total_tokens": 16,
                    },
                }
            ).encode("utf-8")

        adapter = OpenAIResponsesAdapter(
            model="test-model",
            base_url="https://example.invalid/v1",
            http_sender=sender,
            retry_policy=RetryPolicy(max_retries=0),
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}, clear=False):
            response = adapter.complete(
                system_prompt="developer rules",
                user_payload="inert user payload",
            )
        self.assertEqual(observed["url"], "https://example.invalid/v1/responses")
        self.assertEqual(observed["body"]["instructions"], "developer rules")
        self.assertEqual(observed["body"]["input"], "inert user payload")
        self.assertEqual(observed["authorization"], "Bearer secret-value")
        self.assertEqual(response.text, "optimized")
        self.assertEqual(response.usage["total_tokens"], 16)
        self.assertNotIn("secret-value", json.dumps(response.to_metadata()))

    def test_retryable_http_error_is_retried(self):
        calls = []

        def sender(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {},
                    None,
                )
            return 200, b'{"output_text":"ok","usage":{"total_tokens":2}}'

        adapter = OpenAIResponsesAdapter(
            model="test-model",
            http_sender=sender,
            retry_policy=RetryPolicy(
                max_retries=1,
                initial_backoff_seconds=0,
                maximum_backoff_seconds=0,
            ),
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}, clear=False):
            response = adapter.complete(system_prompt="s", user_payload="u")
        self.assertEqual(response.attempts, 2)
        self.assertEqual(len(calls), 2)

    def test_missing_key_fails_without_request(self):
        adapter = OpenAIResponsesAdapter(model="test-model")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AdapterError, "OPENAI_API_KEY"):
                adapter.complete(system_prompt="s", user_payload="u")

    def test_precancelled_request_never_sends(self):
        called = False

        def sender(request, timeout):
            nonlocal called
            called = True
            return 200, b'{"output_text":"unexpected"}'

        token = CancellationToken()
        token.cancel()
        adapter = OpenAIResponsesAdapter(model="test-model", http_sender=sender)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}, clear=False):
            with self.assertRaises(AdapterCancelled):
                adapter.complete(system_prompt="s", user_payload="u", cancellation=token)
        self.assertFalse(called)

    def test_inflight_request_cancels_promptly(self):
        started = threading.Event()

        def sender(request, timeout):
            started.set()
            time.sleep(0.5)
            return 200, b'{"output_text":"late"}'

        token = CancellationToken()
        adapter = OpenAIResponsesAdapter(
            model="test-model",
            http_sender=sender,
            timeout_seconds=1,
            retry_policy=RetryPolicy(max_retries=0),
        )

        def cancel() -> None:
            started.wait(0.2)
            token.cancel()

        threading.Thread(target=cancel, daemon=True).start()
        began = time.monotonic()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}, clear=False):
            with self.assertRaises(AdapterCancelled):
                adapter.complete(
                    system_prompt="s",
                    user_payload="u",
                    cancellation=token,
                )
        self.assertLess(time.monotonic() - began, 0.3)

    def test_structured_output_shape(self):
        config = OpenAIResponsesAdapter.structured_output(
            name="result",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        )
        self.assertEqual(config["text"]["format"]["type"], "json_schema")
        self.assertTrue(config["text"]["format"]["strict"])


class ExternalCommandAdapterTests(unittest.TestCase):
    def permissions(self, timeout=1.0):
        return ToolPermissionManifest(
            allowed_executables=(sys.executable,),
            maximum_timeout_seconds=timeout,
        )

    def test_json_command_contract(self):
        code = (
            "import json,sys;"
            "p=json.load(sys.stdin);"
            "print(json.dumps({'output_text':p['instructions']+'|'+p['input'],"
            "'usage':{'total_tokens':7},'response_id':'local-1'}))"
        )
        adapter = ExternalCommandAdapter(
            command=(sys.executable, "-c", code),
            permissions=self.permissions(),
            timeout_seconds=0.5,
        )
        response = adapter.complete(system_prompt="rules", user_payload="payload")
        self.assertEqual(response.text, "rules|payload")
        self.assertEqual(response.usage["total_tokens"], 7)
        self.assertEqual(response.response_id, "local-1")

    def test_non_allowlisted_executable_fails(self):
        adapter = ExternalCommandAdapter(
            command=(sys.executable, "-c", "print('x')"),
            permissions=ToolPermissionManifest(
                allowed_executables=(str(Path(sys.executable).with_name("other.exe")),),
            ),
        )
        with self.assertRaisesRegex(AdapterError, "not allowlisted"):
            adapter.complete(system_prompt="s", user_payload="u")

    def test_timeout_is_deterministic(self):
        adapter = ExternalCommandAdapter(
            command=(
                sys.executable,
                "-c",
                "import time; time.sleep(0.3); print('late')",
            ),
            permissions=self.permissions(timeout=0.2),
            timeout_seconds=0.05,
        )
        started = time.monotonic()
        with self.assertRaisesRegex(AdapterError, "timed out"):
            adapter.complete(system_prompt="s", user_payload="u")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_sensitive_environment_requires_explicit_permission(self):
        with self.assertRaisesRegex(ValueError, "Sensitive environment"):
            ToolPermissionManifest(
                allowed_executables=(sys.executable,),
                allowed_environment=("OPENAI_API_KEY",),
            ).validate()


class CodexExecAdapterTests(unittest.TestCase):
    def test_usage_limit_event_is_reported_as_quota_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "fake_codex.py"
            fake.write_text(
                """
import json
import sys

sys.stdin.read()
print(json.dumps({
    "type": "turn.failed",
    "error": {
        "message": "You've hit your usage limit. Try again at 02:17."
    }
}))
raise SystemExit(1)
""".strip(),
                encoding="utf-8",
            )
            adapter = CodexExecAdapter(
                model="test-codex-model",
                command_prefix=(sys.executable, str(fake)),
                working_directory=root,
                timeout_seconds=2,
            )
            with self.assertRaisesRegex(
                AdapterQuotaError,
                "usage limit.*02:17",
            ):
                adapter.complete(system_prompt="secret prompt", user_payload="secret data")

    def test_jsonl_usage_and_final_message_are_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "fake_codex.py"
            fake.write_text(
                """
import json
import sys
from pathlib import Path

prompt = sys.stdin.read()
output = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
output.write_text(prompt, encoding="utf-8")
print(json.dumps({"type": "thread.started", "thread_id": "thread-test"}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {
        "input_tokens": 20,
        "output_tokens": 5,
        "reasoning_output_tokens": 1
    }
}))
""".strip(),
                encoding="utf-8",
            )
            adapter = CodexExecAdapter(
                model="test-codex-model",
                command_prefix=(sys.executable, str(fake)),
                working_directory=root,
                timeout_seconds=2,
            )
            response = adapter.complete(
                system_prompt="optimizer rules",
                user_payload="runtime data",
            )
            self.assertIn("Local files and tools are intentionally outside", response.text)
            self.assertNotIn("Do not use tools.", response.text)
            self.assertEqual(response.response_id, "thread-test")
            self.assertEqual(response.usage["input_tokens"], 20)
            self.assertEqual(response.provider, "openai-codex")


if __name__ == "__main__":
    unittest.main()
