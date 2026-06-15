import os
import unittest
from unittest.mock import patch

from prompt_performance_engine.software_sandbox import (
    DockerSandbox,
    DockerSandboxPolicy,
)


IMAGE = (
    "python:3.13-alpine@sha256:"
    "db66119d6609a3a941a9433b225f4e13d33c459cede097cf3ec2fc4d1bd314b2"
)


def matching_inspect(policy: DockerSandboxPolicy) -> dict:
    return {
        "Config": {"User": policy.user},
        "HostConfig": {
            "NetworkMode": policy.network_mode,
            "ReadonlyRootfs": True,
            "Privileged": False,
            "Binds": None,
            "Mounts": [],
            "Devices": [],
            "DeviceRequests": [],
            "PidMode": "",
            "IpcMode": "private",
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": policy.pids_limit,
            "Memory": policy.memory_bytes,
            "MemorySwap": policy.memory_swap_bytes,
            "NanoCpus": policy.nano_cpus,
            "Tmpfs": {
                "/tmp": (
                    "rw,noexec,nosuid,nodev,"
                    f"size={policy.tmpfs_size_bytes}"
                )
            },
        },
    }


class DockerSandboxContractTests(unittest.TestCase):
    def test_requires_immutable_image_digest(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            with self.assertRaisesRegex(ValueError, "immutable sha256"):
                DockerSandbox("python:3.13-alpine")

    def test_runtime_policy_match_requires_all_boundaries(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        record = matching_inspect(sandbox.policy)
        self.assertTrue(sandbox._policy_matches(record))

        record["HostConfig"]["NetworkMode"] = "bridge"
        self.assertFalse(sandbox._policy_matches(record))

    def test_command_is_fail_closed_and_has_no_host_mount(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        command = sandbox._base_command("test-container")

        self.assertEqual(command[1], "create")
        self.assertIn("never", command)
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("--memory", command)
        self.assertIn("--pids-limit", command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("-v", command)

    def test_policy_cannot_weaken_required_boundaries(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            with self.assertRaisesRegex(ValueError, "weakens"):
                DockerSandbox(
                    IMAGE,
                    policy=DockerSandboxPolicy(network_mode="bridge"),
                )


@unittest.skipUnless(
    os.environ.get("PPE_TEST_DOCKER_SANDBOX_IMAGE"),
    "Set PPE_TEST_DOCKER_SANDBOX_IMAGE for real Docker integration tests.",
)
class DockerSandboxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = DockerSandbox(
            os.environ["PPE_TEST_DOCKER_SANDBOX_IMAGE"]
        )

    def test_network_filesystem_identity_and_runtime_policy(self):
        result = self.sandbox.verify_isolation()
        self.assertTrue(result.passed, result.detail)
        self.assertTrue(result.policy_verified)
        self.assertTrue(result.image_id.startswith("sha256:"))
        self.assertIsNotNone(result.python_version)

    def test_infinite_loop_is_terminated(self):
        result = self.sandbox.run_script(
            "while True: pass",
            timeout_seconds=1,
        )
        self.assertFalse(result.passed)
        self.assertTrue(result.timed_out)

    def test_memory_exhaustion_is_stopped_by_cgroup_limit(self):
        result = self.sandbox.run_script(
            "chunks = []\n"
            "while True:\n"
            "    chunks.append(bytearray(8 * 1024 * 1024))\n",
            timeout_seconds=8,
        )
        self.assertFalse(result.passed)
        self.assertTrue(result.oom_killed)
        self.assertEqual(result.exit_code, 137)


if __name__ == "__main__":
    unittest.main()
