import hashlib
import unittest

from prompt_performance_engine.audit import audit_prompt
from prompt_performance_engine.contracts import PACKAGE_VERSION
from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.validation import validate_artifact


def valid_artifact():
    source = "source"
    optimized = "Produce a complete deliverable."
    source_audit = audit_prompt(source)
    optimized_audit = audit_prompt(optimized, source_prompt=source)
    artifact = {
        "schema_version": "1.0.0",
        "package_version": PACKAGE_VERSION,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "optimized_prompt": optimized,
        "domain": "generic",
        "architecture": "direct",
        "runtime": {
            "model_calls": [
                {
                    "provider": "test",
                    "model": "test-model",
                    "response_id": "response-1",
                    "usage": {"total_tokens": 10},
                    "attempts": 1,
                    "elapsed_ms": 1,
                    "status": "completed",
                }
            ],
            "total_calls": 1,
            "total_usage": {"total_tokens": 10},
        },
        "audit": {
            "source": source_audit.to_dict(),
            "optimized": optimized_audit.to_dict(),
        },
        "evidence": {
            "level": "E1",
            "status": "candidate",
            "claim": "optimized_candidate",
            "limitations": ["No runtime comparison."],
        },
    }
    artifact["artifact_payload_sha256"] = hash_payload(
        artifact,
        "artifact_payload_sha256",
    )
    return artifact


class ArtifactValidationTests(unittest.TestCase):
    def test_valid_artifact(self):
        self.assertEqual(validate_artifact(valid_artifact()), [])

    def test_version_mismatch_fails(self):
        artifact = valid_artifact()
        artifact["schema_version"] = "4.0"
        self.assertTrue(validate_artifact(artifact))

    def test_unproved_verified_status_fails(self):
        artifact = valid_artifact()
        artifact["evidence"]["status"] = "verified_scoped"
        self.assertTrue(validate_artifact(artifact))

    def test_tampered_audit_status_fails(self):
        artifact = valid_artifact()
        artifact["audit"]["optimized"]["passed"] = False
        artifact["artifact_payload_sha256"] = hash_payload(
            artifact,
            "artifact_payload_sha256",
        )
        self.assertTrue(validate_artifact(artifact))

    def test_tampered_payload_hash_fails(self):
        artifact = valid_artifact()
        artifact["domain"] = "software_engineering"
        self.assertTrue(validate_artifact(artifact))

    def test_runtime_usage_mismatch_fails(self):
        artifact = valid_artifact()
        artifact["runtime"]["total_usage"]["total_tokens"] = 11
        artifact["artifact_payload_sha256"] = hash_payload(
            artifact,
            "artifact_payload_sha256",
        )
        self.assertTrue(validate_artifact(artifact))

    def test_tampered_candidate_selection_fails(self):
        artifact = valid_artifact()
        prompt = artifact["optimized_prompt"]
        artifact["runtime"]["selection"] = {
            "method": "single_candidate",
            "candidate_count": 1,
            "selected_index": 1,
            "selector_response_sha256": None,
            "candidates": [
                {
                    "index": 1,
                    "prompt": prompt + " tampered",
                    "prompt_sha256": hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest(),
                    "selected": True,
                }
            ],
        }
        artifact["artifact_payload_sha256"] = hash_payload(
            artifact,
            "artifact_payload_sha256",
        )
        self.assertTrue(validate_artifact(artifact))

    def test_malformed_candidate_selection_fails_without_exception(self):
        artifact = valid_artifact()
        artifact["runtime"]["selection"] = {
            "method": "model_selector",
            "candidate_count": None,
            "selected_index": True,
            "selector_response_sha256": None,
            "candidates": [
                {
                    "index": True,
                    "prompt": artifact["optimized_prompt"],
                    "prompt_sha256": hashlib.sha256(
                        artifact["optimized_prompt"].encode("utf-8")
                    ).hexdigest(),
                    "selected": True,
                }
            ],
        }
        artifact["artifact_payload_sha256"] = hash_payload(
            artifact,
            "artifact_payload_sha256",
        )
        violations = validate_artifact(artifact)
        self.assertTrue(violations)
        self.assertTrue(all(item.rule_id == "A09" for item in violations))

    def test_mojibake_fails(self):
        artifact = valid_artifact()
        artifact["optimized_prompt"] = (
            r"\u6d7c\u6a0a\u5bee\u5bf2\u935a\u5eb7\u6bb7 Prompt"
            .encode("ascii")
            .decode("unicode_escape")
        )
        self.assertTrue(validate_artifact(artifact))


if __name__ == "__main__":
    unittest.main()
