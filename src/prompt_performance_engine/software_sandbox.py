"""Docker-backed isolation for trusted software-verification harnesses."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any


MAX_PIDS_LIMIT = 64
MAX_MEMORY_BYTES = 128 * 1024 * 1024
MAX_NANO_CPUS = 500_000_000
MAX_TMPFS_SIZE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class DockerSandboxPolicy:
    network_mode: str = "none"
    read_only_root: bool = True
    cap_drop: tuple[str, ...] = ("ALL",)
    no_new_privileges: bool = True
    pids_limit: int = MAX_PIDS_LIMIT
    memory_bytes: int = MAX_MEMORY_BYTES
    memory_swap_bytes: int = MAX_MEMORY_BYTES
    nano_cpus: int = MAX_NANO_CPUS
    user: str = "65534:65534"
    tmpfs_size_bytes: int = MAX_TMPFS_SIZE_BYTES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SandboxRun:
    passed: bool
    detail: str
    stdout: str
    stderr: str
    exit_code: int | None
    elapsed_ms: int
    timed_out: bool
    oom_killed: bool
    image_reference: str
    image_id: str
    python_version: str | None
    probe_facts: dict[str, Any]
    policy: dict[str, Any]
    policy_verified: bool
    exit_state_verified: bool = False


class DockerSandbox:
    def __init__(
        self,
        image: str,
        *,
        docker_command: str = "docker",
        policy: DockerSandboxPolicy | None = None,
    ) -> None:
        if not image.strip():
            raise ValueError("Docker sandbox image must not be empty.")
        if not re.search(r"@sha256:[0-9a-f]{64}$", image):
            raise ValueError(
                "Docker sandbox image must use an immutable sha256 digest."
            )
        resolved = shutil.which(docker_command)
        if resolved is None:
            raise ValueError(f"Docker command was not found: {docker_command}")
        self.image = image
        self.docker_command = resolved
        self.policy = policy or DockerSandboxPolicy()
        if (
            self.policy.network_mode != "none"
            or not self.policy.read_only_root
            or self.policy.cap_drop != ("ALL",)
            or not self.policy.no_new_privileges
            or self.policy.pids_limit <= 0
            or self.policy.pids_limit > MAX_PIDS_LIMIT
            or self.policy.memory_bytes <= 0
            or self.policy.memory_bytes > MAX_MEMORY_BYTES
            or self.policy.memory_swap_bytes != self.policy.memory_bytes
            or self.policy.nano_cpus <= 0
            or self.policy.nano_cpus > MAX_NANO_CPUS
            or self.policy.user != DockerSandboxPolicy().user
            or self.policy.tmpfs_size_bytes <= 0
            or self.policy.tmpfs_size_bytes > MAX_TMPFS_SIZE_BYTES
        ):
            raise ValueError("Docker sandbox policy weakens a required boundary.")

    def _base_command(self, name: str) -> list[str]:
        policy = self.policy
        command = [
            self.docker_command,
            "create",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            policy.network_mode,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(policy.pids_limit),
            "--memory",
            str(policy.memory_bytes),
            "--memory-swap",
            str(policy.memory_swap_bytes),
            "--cpus",
            str(policy.nano_cpus / 1_000_000_000),
            "--user",
            policy.user,
            "--tmpfs",
            (
                "/tmp:rw,noexec,nosuid,nodev,size="
                f"{policy.tmpfs_size_bytes}"
            ),
            "--workdir",
            "/tmp",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONHASHSEED=0",
            "--entrypoint",
            "python",
            "-i",
            self.image,
            "-I",
            "-S",
            "-",
        ]
        return command

    def _inspect(self, name: str) -> dict[str, Any]:
        completed = subprocess.run(
            [self.docker_command, "inspect", name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Docker inspect failed: "
                + (completed.stderr.strip() or "unknown error")
            )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, list) or len(payload) != 1:
            raise RuntimeError("Docker inspect returned an invalid payload.")
        record = payload[0]
        if not isinstance(record, dict):
            raise RuntimeError("Docker inspect record is invalid.")
        return record

    def _policy_matches(self, record: dict[str, Any]) -> bool:
        host = record.get("HostConfig", {})
        config = record.get("Config", {})
        effective_mounts = record.get("Mounts")
        if not isinstance(host, dict) or not isinstance(config, dict):
            return False
        if not isinstance(effective_mounts, list) or len(effective_mounts) > 1:
            return False
        if any(
            not isinstance(mount, dict)
            or str(mount.get("Type", "")).lower() != "tmpfs"
            or mount.get("Destination") != "/tmp"
            or mount.get("RW") is not True
            for mount in effective_mounts
        ):
            return False
        policy = self.policy
        security_options = {
            str(item).lower() for item in host.get("SecurityOpt") or []
        }
        cap_drop = {str(item).upper() for item in host.get("CapDrop") or []}
        tmpfs = host.get("Tmpfs")
        if not isinstance(tmpfs, dict) or set(tmpfs) != {"/tmp"}:
            return False
        tmpfs_options = [
            item.strip().lower()
            for item in str(tmpfs.get("/tmp", "")).split(",")
            if item.strip()
        ]
        expected_tmpfs_options = {
            "rw",
            "noexec",
            "nosuid",
            "nodev",
            f"size={policy.tmpfs_size_bytes}",
        }
        return all(
            (
                host.get("NetworkMode") == policy.network_mode,
                host.get("ReadonlyRootfs") is policy.read_only_root,
                host.get("Privileged") is False,
                not host.get("Binds"),
                not host.get("Mounts"),
                not host.get("Devices"),
                not host.get("DeviceRequests"),
                host.get("PidMode") in {"", "private"},
                host.get("IpcMode") in {"", "private"},
                "ALL" in cap_drop,
                not host.get("CapAdd"),
                not host.get("GroupAdd"),
                security_options
                in (
                    {"no-new-privileges"},
                    {"no-new-privileges:true"},
                ),
                host.get("PidsLimit") == policy.pids_limit,
                host.get("Memory") == policy.memory_bytes,
                host.get("MemorySwap") == policy.memory_swap_bytes,
                host.get("NanoCpus") == policy.nano_cpus,
                config.get("User") == policy.user,
                len(tmpfs_options) == len(expected_tmpfs_options),
                set(tmpfs_options) == expected_tmpfs_options,
            )
        )

    @staticmethod
    def _decode_process_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def run_script(
        self,
        script: str,
        *,
        timeout_seconds: float = 8.0,
    ) -> SandboxRun:
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Sandbox timeout must be between 0 and 60 seconds.")
        name = f"ppe-sandbox-{uuid.uuid4().hex}"
        started = time.monotonic()
        completed: subprocess.CompletedProcess[str] | None = None
        timed_out = False
        inspect_record: dict[str, Any] = {}
        cleanup_required = False
        create_outcome_uncertain = False
        try:
            cleanup_required = True
            create_outcome_uncertain = True
            try:
                created = subprocess.run(
                    self._base_command(name),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise
            create_outcome_uncertain = False
            if created.returncode != 0:
                raise RuntimeError(
                    "Docker create failed: "
                    + (created.stderr.strip() or "unknown error")
                )
            inspect_record = self._inspect(name)
            if not self._policy_matches(inspect_record):
                raise RuntimeError(
                    "Docker runtime policy did not match before execution."
                )
            try:
                completed = subprocess.run(
                    [
                        self.docker_command,
                        "start",
                        "--attach",
                        "--interactive",
                        name,
                    ],
                    input=script,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                completed = subprocess.CompletedProcess(
                    args=exc.cmd,
                    returncode=None,
                    stdout=self._decode_process_text(exc.stdout),
                    stderr=self._decode_process_text(exc.stderr),
                )
            inspect_record = self._inspect(name)
        finally:
            if cleanup_required:
                try:
                    removed = subprocess.run(
                        [self.docker_command, "rm", "--force", name],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=15,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise RuntimeError(
                        "Docker sandbox cleanup failed or timed out; container "
                        "removal is not proven."
                    ) from exc
                removal_error = (removed.stderr or "").strip()
                definitely_absent = (
                    not create_outcome_uncertain
                    and "no such container" in removal_error.lower()
                )
                if removed.returncode != 0 and not definitely_absent:
                    raise RuntimeError(
                        "Docker sandbox cleanup failed; container removal is not "
                        "proven: "
                        + (removal_error or "unknown error")
                    )

        elapsed_ms = round((time.monotonic() - started) * 1000)
        policy_verified = self._policy_matches(inspect_record)
        state = inspect_record.get("State")
        state_valid = isinstance(state, dict)
        if not state_valid:
            state = {}
        oom_killed = state.get("OOMKilled") is True
        image_id = str(inspect_record.get("Image", ""))
        stdout = completed.stdout if completed is not None else ""
        stderr = completed.stderr if completed is not None else ""
        client_exit_code = (
            completed.returncode
            if completed is not None
            and isinstance(completed.returncode, int)
            and not isinstance(completed.returncode, bool)
            else None
        )
        state_exit_code = (
            state.get("ExitCode")
            if isinstance(state.get("ExitCode"), int)
            and not isinstance(state.get("ExitCode"), bool)
            else None
        )
        state_consistent = (
            state_valid
            and state.get("Running") is False
            and state_exit_code is not None
            and state_exit_code == client_exit_code
        )
        exit_code = state_exit_code
        passed = (
            not timed_out
            and state_consistent
            and not oom_killed
            and exit_code == 0
            and policy_verified
        )
        if timed_out:
            detail = f"Docker sandbox timed out after {timeout_seconds:g}s."
        elif not policy_verified:
            detail = "Docker runtime policy did not match the required isolation."
        elif not state_consistent:
            detail = "Docker sandbox exit state did not match the attached process."
        elif oom_killed:
            detail = "Docker sandbox process was terminated by the memory limit."
        elif exit_code != 0:
            final_error = stderr.strip().splitlines()
            detail = (
                "Docker sandbox execution failed: "
                + (final_error[-1] if final_error else f"exit code {exit_code}")
            )
        else:
            detail = "Docker sandbox execution passed with verified runtime policy."
        python_version = None
        probe_facts: dict[str, Any] = {}
        for line in stdout.splitlines():
            if line.startswith("PPE_PYTHON_VERSION="):
                python_version = line.split("=", 1)[1]
            if line.startswith("PPE_SANDBOX_FACTS="):
                try:
                    candidate = json.loads(line.split("=", 1)[1])
                except json.JSONDecodeError:
                    candidate = None
                if isinstance(candidate, dict):
                    probe_facts = candidate
        return SandboxRun(
            passed=passed,
            detail=detail,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code if isinstance(exit_code, int) else None,
            elapsed_ms=elapsed_ms,
            timed_out=timed_out,
            oom_killed=oom_killed,
            image_reference=self.image,
            image_id=image_id,
            python_version=python_version,
            probe_facts=probe_facts,
            policy=self.policy.to_dict(),
            policy_verified=policy_verified,
            exit_state_verified=state_consistent,
        )

    def verify_isolation(self) -> SandboxRun:
        script = r'''
import json
import os
import socket
import sys

network_blocked = False
sock = socket.socket()
sock.settimeout(0.25)
try:
    sock.connect(("1.1.1.1", 53))
except OSError:
    network_blocked = True
finally:
    sock.close()

root_read_only = False
try:
    with open("/ppe-root-write-probe", "w", encoding="utf-8") as handle:
        handle.write("unexpected")
except OSError:
    root_read_only = True

tmp_writable = False
try:
    with open("/tmp/ppe-write-probe", "w", encoding="utf-8") as handle:
        handle.write("ok")
    os.unlink("/tmp/ppe-write-probe")
    tmp_writable = True
except OSError:
    pass

facts = {
    "network_blocked": network_blocked,
    "root_read_only": root_read_only,
    "tmp_writable": tmp_writable,
    "non_root": (
        os.geteuid() != 0
        and os.getegid() != 0
        and all(group != 0 for group in os.getgroups())
    ),
}
print("PPE_PYTHON_VERSION=" + sys.version.split()[0])
print("PPE_SANDBOX_FACTS=" + json.dumps(facts, sort_keys=True))
if not all(facts.values()):
    raise SystemExit(2)
'''
        return self.run_script(script, timeout_seconds=8.0)

    def verify_resource_limits(self) -> dict[str, SandboxRun]:
        timeout_probe = self.run_script(
            "while True: pass",
            timeout_seconds=1.0,
        )
        memory_probe = self.run_script(
            "chunks = []\n"
            "while True:\n"
            "    chunks.append(bytearray(8 * 1024 * 1024))\n",
            timeout_seconds=8.0,
        )
        return {
            "timeout": timeout_probe,
            "memory": memory_probe,
        }
