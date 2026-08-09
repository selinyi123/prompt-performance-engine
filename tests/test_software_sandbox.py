import os
import subprocess
import unittest
from unittest.mock import patch

from prompt_performance_engine.software_sandbox import (
    DockerSandbox,
    DockerSandboxPolicy,
)
from prompt_performance_engine.software_execution import verify_pagination


IMAGE = (
    "python:3.13-alpine@sha256:"
    "db66119d6609a3a941a9433b225f4e13d33c459cede097cf3ec2fc4d1bd314b2"
)


def matching_inspect(policy: DockerSandboxPolicy) -> dict:
    return {
        "Config": {"User": policy.user},
        "State": {"Running": False, "ExitCode": 0, "OOMKilled": False},
        "Image": "sha256:" + "d" * 64,
        "Mounts": [],
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
            "CapAdd": [],
            "GroupAdd": [],
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

    def test_runtime_policy_rejects_disabled_no_new_privileges(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        record = matching_inspect(sandbox.policy)
        record["HostConfig"]["SecurityOpt"] = ["no-new-privileges:false"]
        self.assertFalse(sandbox._policy_matches(record))

    def test_runtime_policy_accepts_docker_desktop_no_new_privileges_shorthand(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        record = matching_inspect(sandbox.policy)
        record["HostConfig"]["SecurityOpt"] = ["no-new-privileges"]
        self.assertTrue(sandbox._policy_matches(record))

    def test_runtime_policy_rejects_added_capabilities(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        record = matching_inspect(sandbox.policy)
        record["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
        self.assertFalse(sandbox._policy_matches(record))

    def test_runtime_policy_rejects_added_groups(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        record = matching_inspect(sandbox.policy)
        record["HostConfig"]["GroupAdd"] = ["0"]
        self.assertFalse(sandbox._policy_matches(record))

    def test_runtime_policy_rejects_joined_process_or_ipc_namespaces(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        for field in ("PidMode", "IpcMode"):
            with self.subTest(field=field):
                record = matching_inspect(sandbox.policy)
                record["HostConfig"][field] = "container:peer"
                self.assertFalse(sandbox._policy_matches(record))

    def test_runtime_policy_rejects_unconfined_security_profiles(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        for option in ("seccomp=unconfined", "apparmor=unconfined"):
            with self.subTest(option=option):
                record = matching_inspect(sandbox.policy)
                record["HostConfig"]["SecurityOpt"].append(option)
                self.assertFalse(sandbox._policy_matches(record))

    def test_runtime_policy_rejects_effective_bind_or_volume_mounts(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        record = matching_inspect(sandbox.policy)
        record["Mounts"] = [
            {
                "Type": "volume",
                "Destination": "/data",
                "RW": True,
            }
        ]
        self.assertFalse(sandbox._policy_matches(record))
        record["Mounts"] = [
            {
                "Type": "tmpfs",
                "Destination": "/tmp",
                "RW": True,
            }
        ]
        self.assertTrue(sandbox._policy_matches(record))

    def test_runtime_policy_rejects_conflicting_or_extra_tmpfs_options(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        for option in ("exec", "suid", "dev", "size=999999999"):
            with self.subTest(option=option):
                record = matching_inspect(sandbox.policy)
                record["HostConfig"]["Tmpfs"]["/tmp"] += f",{option}"
                self.assertFalse(sandbox._policy_matches(record))

    def test_cleanup_failure_is_fail_closed(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        completed = (
            subprocess.CompletedProcess(args=["docker", "create"], returncode=0),
            subprocess.CompletedProcess(
                args=["docker", "start"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["docker", "rm"],
                returncode=1,
                stdout="",
                stderr="permission denied",
            ),
        )
        with patch.object(
            sandbox,
            "_inspect",
            return_value=matching_inspect(sandbox.policy),
        ), patch(
            "prompt_performance_engine.software_sandbox.subprocess.run",
            side_effect=completed,
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                sandbox.run_script("print('ok')")

    def test_create_timeout_still_attempts_cleanup(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        calls = (
            subprocess.TimeoutExpired(cmd=["docker", "create"], timeout=30),
            subprocess.CompletedProcess(
                args=["docker", "rm"], returncode=0, stdout="", stderr=""
            ),
        )
        with patch(
            "prompt_performance_engine.software_sandbox.subprocess.run",
            side_effect=calls,
        ) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                sandbox.run_script("print('ok')")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[-1].args[0][1:3], ["rm", "--force"])

    def test_failed_create_still_attempts_cleanup(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        calls = (
            subprocess.CompletedProcess(
                args=["docker", "create"],
                returncode=1,
                stdout="",
                stderr="create rejected",
            ),
            subprocess.CompletedProcess(
                args=["docker", "rm"],
                returncode=1,
                stdout="",
                stderr="Error response from daemon: No such container: test",
            ),
        )
        with patch(
            "prompt_performance_engine.software_sandbox.subprocess.run",
            side_effect=calls,
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "Docker create failed"):
                sandbox.run_script("print('never starts')")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[-1].args[0][1:3], ["rm", "--force"])

    def test_timeout_bytes_are_decoded_before_result_parsing(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        calls = (
            subprocess.CompletedProcess(args=["docker", "create"], returncode=0),
            subprocess.TimeoutExpired(
                cmd=["docker", "start"],
                timeout=1,
                output=b"candidate output before hang\n",
                stderr=b"candidate error before hang\n",
            ),
            subprocess.CompletedProcess(
                args=["docker", "rm"], returncode=0, stdout="", stderr=""
            ),
        )
        with patch.object(
            sandbox,
            "_inspect",
            return_value=matching_inspect(sandbox.policy),
        ), patch(
            "prompt_performance_engine.software_sandbox.subprocess.run",
            side_effect=calls,
        ):
            result = sandbox.run_script("while True: pass", timeout_seconds=1)
        self.assertTrue(result.timed_out)
        self.assertIsInstance(result.stdout, str)
        self.assertIn("candidate output", result.stdout)

    def test_post_inspect_exit_state_overrides_conflicting_client_success(self):
        with patch(
            "prompt_performance_engine.software_sandbox.shutil.which",
            return_value="docker",
        ):
            sandbox = DockerSandbox(IMAGE)
        final_inspect = matching_inspect(sandbox.policy)
        final_inspect["State"] = {
            "Running": False,
            "ExitCode": 137,
            "OOMKilled": True,
        }
        calls = (
            subprocess.CompletedProcess(args=["docker", "create"], returncode=0),
            subprocess.CompletedProcess(
                args=["docker", "start"],
                returncode=0,
                stdout='PPE_PYTHON_VERSION=3.13.14\n{"status": "passed"}\n',
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["docker", "rm"], returncode=0, stdout="", stderr=""
            ),
        )
        with patch.object(
            sandbox,
            "_inspect",
            side_effect=[matching_inspect(sandbox.policy), final_inspect],
        ), patch(
            "prompt_performance_engine.software_sandbox.subprocess.run",
            side_effect=calls,
        ):
            result = sandbox.run_script("print('claimed success')")
        self.assertFalse(result.passed)
        self.assertTrue(result.oom_killed)
        self.assertEqual(result.exit_code, 137)
        self.assertFalse(result.exit_state_verified)
        self.assertIn("exit state did not match", result.detail)

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
            weak_policies = (
                DockerSandboxPolicy(network_mode="bridge"),
                DockerSandboxPolicy(pids_limit=65),
                DockerSandboxPolicy(
                    memory_bytes=256 * 1024 * 1024,
                    memory_swap_bytes=256 * 1024 * 1024,
                ),
                DockerSandboxPolicy(nano_cpus=750_000_000),
                DockerSandboxPolicy(tmpfs_size_bytes=32 * 1024 * 1024),
                DockerSandboxPolicy(user="0:65534"),
                DockerSandboxPolicy(user="00:00"),
                DockerSandboxPolicy(user="root:65534"),
                DockerSandboxPolicy(user="65534:0"),
            )
            for policy in weak_policies:
                with self.subTest(policy=policy):
                    with self.assertRaisesRegex(ValueError, "weakens"):
                        DockerSandbox(IMAGE, policy=policy)


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

    def test_software_contract_executes_in_verified_docker(self):
        output = """```python
def paginate(items, page, page_size):
    if isinstance(page, bool) or not isinstance(page, int):
        raise TypeError("invalid page")
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise TypeError("invalid page size")
    if page < 1 or page_size < 1:
        raise ValueError("must be positive")
    start = (page - 1) * page_size
    return items[start:start + page_size]
```"""

        passed, detail = verify_pagination(output, sandbox=self.sandbox)

        self.assertTrue(passed, detail)


if __name__ == "__main__":
    unittest.main()
