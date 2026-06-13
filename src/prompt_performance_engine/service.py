"""Persistent local HTTP service for Prompt optimization jobs."""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .adapters import ModelAdapter
from .contracts import OptimizationRequest
from .hashing import canonical_json_bytes, sha256_json
from .runtime import optimize


LOGGER = logging.getLogger("prompt_performance_engine.service")
MAX_REQUEST_BYTES = 1_000_000
RATE_LIMIT_PER_MINUTE = 120


class IdempotencyConflict(ValueError):
    pass


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_path TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def recover_interrupted(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'queued',
                    error = 'Recovered after service restart.',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (time.time(),),
            )
            return int(cursor.rowcount)

    def create_or_get(
        self,
        request: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        request_sha256 = sha256_json(request)
        request_json = canonical_json_bytes(request).decode("utf-8")
        now = time.time()
        job_id = secrets.token_hex(16)
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        job_id, idempotency_key, request_sha256, request_json,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        job_id,
                        idempotency_key,
                        request_sha256,
                        request_json,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.get_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            if existing["request_sha256"] != request_sha256:
                raise IdempotencyConflict(
                    "Idempotency key was already used for a different request."
                ) from None
            return existing, False
        created = self.get(job_id)
        if created is None:
            raise RuntimeError("Created job could not be loaded.")
        return created, True

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def queued_job_ids(self) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def mark_running(self, job_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'running', error = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (time.time(), job_id),
            )
            return cursor.rowcount == 1

    def mark_succeeded(self, job_id: str, artifact_path: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', artifact_path = ?, error = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (artifact_path, time.time(), job_id),
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (error[:500], time.time(), job_id),
            )

    def request_for(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return json.loads(job["request_json"])

    def health(self) -> bool:
        try:
            with self._connection() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, job_id: str, artifact: dict[str, Any]) -> str:
        if not job_id.isalnum():
            raise ValueError("Invalid job identifier.")
        destination = (self.root / f"{job_id}.json").resolve()
        destination.relative_to(self.root)
        temporary = self.root / f".{job_id}.{secrets.token_hex(4)}.tmp"
        payload = json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, destination)
        return destination.name

    def read(self, relative: str) -> dict[str, Any]:
        path = (self.root / relative).resolve()
        path.relative_to(self.root)
        return json.loads(path.read_text(encoding="utf-8"))

    def health(self) -> bool:
        return self.root.is_dir() and os.access(self.root, os.W_OK)


@dataclass
class ServiceMetrics:
    submitted: int = 0
    idempotent_replays: int = 0
    succeeded: int = 0
    failed: int = 0
    recovered: int = 0
    rate_limited: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, name, int(getattr(self, name)) + amount)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "submitted": self.submitted,
                "idempotent_replays": self.idempotent_replays,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "recovered": self.recovered,
                "rate_limited": self.rate_limited,
            }


class OptimizationService:
    def __init__(
        self,
        *,
        store: JobStore,
        artifacts: ArtifactStore,
        adapter_factory: Callable[[], ModelAdapter],
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.adapter_factory = adapter_factory
        self.metrics = ServiceMetrics()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stopping = threading.Event()
        recovered = self.store.recover_interrupted()
        self.metrics.increment("recovered", recovered)
        for job_id in self.store.queued_job_ids():
            self._queue.put(job_id)
        self._worker = threading.Thread(
            target=self._work,
            name="prompt-performance-worker",
            daemon=True,
        )
        self._worker.start()

    @staticmethod
    def _request_from_data(data: dict[str, Any]) -> OptimizationRequest:
        return OptimizationRequest(
            source_prompt=data["source_prompt"],
            mode=data.get("mode", "maximum_quality"),
            output_format=data.get("output_format", "standard"),
            domain=data.get("domain"),
            audience=data.get("audience"),
            target_model=data.get("target_model"),
            target_surface=data.get("target_surface", "api"),
            required_behaviors=tuple(data.get("required_behaviors", [])),
            forbidden_changes=tuple(data.get("forbidden_changes", [])),
            schema_version=data.get("schema_version", "1.0.0"),
        )

    def submit(
        self,
        request_data: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = self._request_from_data(request_data)
        request.validate()
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("Idempotency-Key must contain 1 to 200 characters.")
        job, created = self.store.create_or_get(
            request.to_dict(),
            idempotency_key,
        )
        if created:
            self.metrics.increment("submitted")
            self._queue.put(job["job_id"])
            LOGGER.info("job_submitted job_id=%s", job["job_id"])
        else:
            self.metrics.increment("idempotent_replays")
        return self.public_job(job)

    @staticmethod
    def public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "artifact_available": bool(job.get("artifact_path")),
            "error": job.get("error"),
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        return self.public_job(job) if job is not None else None

    def get_artifact(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        if job is None or not job.get("artifact_path"):
            return None
        return self.artifacts.read(job["artifact_path"])

    def health(self) -> dict[str, Any]:
        checks = {
            "database": self.store.health(),
            "artifact_store": self.artifacts.health(),
            "worker": self._worker.is_alive() and not self._stopping.is_set(),
        }
        return {
            "status": "ok" if all(checks.values()) else "degraded",
            "checks": checks,
        }

    def _work(self) -> None:
        while not self._stopping.is_set():
            job_id = self._queue.get()
            if job_id is None:
                self._queue.task_done()
                return
            try:
                if not self.store.mark_running(job_id):
                    continue
                request = self._request_from_data(self.store.request_for(job_id))
                result = optimize(request, self.adapter_factory())
                relative = self.artifacts.write(job_id, result.artifact)
                self.store.mark_succeeded(job_id, relative)
                self.metrics.increment("succeeded")
                LOGGER.info("job_succeeded job_id=%s", job_id)
            except Exception as exc:
                self.store.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
                self.metrics.increment("failed")
                LOGGER.exception("job_failed job_id=%s", job_id)
            finally:
                self._queue.task_done()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        self._queue.put(None)
        self._worker.join(timeout)


def make_handler(
    service: OptimizationService,
    *,
    service_token: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PromptPerformanceEngine/0"
        _rate_lock = threading.Lock()
        _rate_windows: dict[str, tuple[float, int]] = {}

        def _json(self, status: int, data: dict[str, Any]) -> None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _authorized(self) -> bool:
            if service_token is None:
                return True
            supplied = self.headers.get("Authorization", "")
            return secrets.compare_digest(supplied, f"Bearer {service_token}")

        def _within_rate_limit(self) -> bool:
            client = self.client_address[0]
            now = time.monotonic()
            with self._rate_lock:
                window_start, count = self._rate_windows.get(client, (now, 0))
                if now - window_start >= 60:
                    window_start, count = now, 0
                count += 1
                self._rate_windows[client] = (window_start, count)
                allowed = count <= RATE_LIMIT_PER_MINUTE
            if not allowed:
                service.metrics.increment("rate_limited")
                self._json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"error": "rate_limit_exceeded"},
                )
            return allowed

        def _require_auth(self) -> bool:
            if not self._within_rate_limit():
                return False
            if self._authorized():
                return True
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return False

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                health = service.health()
                status = (
                    HTTPStatus.OK
                    if health["status"] == "ok"
                    else HTTPStatus.SERVICE_UNAVAILABLE
                )
                self._json(status, health)
                return
            if not self._require_auth():
                return
            if path == "/metrics":
                self._json(HTTPStatus.OK, service.metrics.snapshot())
                return
            if path.startswith("/v1/jobs/"):
                job_id = path.removeprefix("/v1/jobs/")
                job = service.get_job(job_id)
                self._json(
                    HTTPStatus.OK if job else HTTPStatus.NOT_FOUND,
                    job or {"error": "job_not_found"},
                )
                return
            if path.startswith("/v1/artifacts/"):
                job_id = path.removeprefix("/v1/artifacts/")
                artifact = service.get_artifact(job_id)
                self._json(
                    HTTPStatus.OK if artifact else HTTPStatus.NOT_FOUND,
                    artifact or {"error": "artifact_not_found"},
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            if not self._require_auth():
                return
            if urlparse(self.path).path != "/v1/optimize":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if content_type != "application/json":
                self._json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error": "content_type_must_be_application_json"},
                )
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
                return
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "request_size_invalid"},
                )
                return
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("request root must be an object")
                job = service.submit(
                    data,
                    idempotency_key=self.headers.get("Idempotency-Key", ""),
                )
            except IdempotencyConflict as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
                return
            except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.ACCEPTED, job)

        def log_message(self, format: str, *args: Any) -> None:
            LOGGER.info("http client=%s message=%s", self.client_address[0], format % args)

    return Handler


def create_http_server(
    service: OptimizationService,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    service_token: str | None = None,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        (host, port),
        make_handler(service, service_token=service_token),
    )
