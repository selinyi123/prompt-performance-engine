"""Command-line interface for the Prompt Performance Engine foundation."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from .adapters import (
    CodexExecAdapter,
    ExternalCommandAdapter,
    MockSequenceAdapter,
    OpenAIResponsesAdapter,
    RetryPolicy,
    ToolPermissionManifest,
)
from .audit import audit_prompt
from .benchmark import load_benchmark_definition, validate_benchmark
from .compiler import compile_request
from .contracts import OptimizationRequest
from .evaluation import (
    ExecutionConfig,
    JudgeDecision,
    RecordedExecutor,
    RecordedJudge,
    evaluate_suite,
    validate_evaluation,
)
from .manifest import build_manifest, verify_manifest
from .migration import import_legacy_audit, migrate_legacy_prompt
from .human_review import (
    aggregate_human_review,
    create_reviewer_packet,
    validate_submission,
)
from .image_review import (
    aggregate_visual_review,
    build_generation_manifest,
    build_reviewer_profile,
    create_visual_review_packet,
    deliver_visual_review_assets,
    validate_generation_manifest,
    validate_visual_submission,
)
from .profiles import load_profiles, resolve_profile
from .readiness import assess_readiness, validate_readiness_report
from .runtime import optimize
from .software_evidence import build_code_execution_evidence
from .software_sandbox import DockerSandbox
from .service import (
    ArtifactStore,
    JobStore,
    OptimizationService,
    create_http_server,
)
from .validation import validate_artifact


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-performance")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("prompt_file", type=Path)
    compile_parser.add_argument("--domain")
    compile_parser.add_argument(
        "--mode",
        choices=["balanced", "maximum_quality", "concise"],
        default="maximum_quality",
    )
    compile_parser.add_argument(
        "--output-format",
        choices=["prompt_only", "standard", "evaluation_package"],
        default="standard",
    )
    compile_parser.add_argument("-o", "--output", type=Path)

    optimize_parser = subparsers.add_parser("optimize")
    optimize_parser.add_argument("prompt_file", type=Path)
    optimize_parser.add_argument(
        "--mock-response",
        type=Path,
        action="append",
        required=True,
        help="Repeat for each candidate and the selector response.",
    )
    optimize_parser.add_argument(
        "--candidate-count", type=int, choices=range(1, 6), default=1
    )
    optimize_parser.add_argument("--domain")
    optimize_parser.add_argument(
        "--mode",
        choices=["balanced", "maximum_quality", "concise"],
        default="maximum_quality",
    )
    optimize_parser.add_argument(
        "--output-format",
        choices=["prompt_only", "standard", "evaluation_package"],
        default="standard",
    )
    optimize_parser.add_argument("--artifact", type=Path)

    openai_parser = subparsers.add_parser("optimize-openai")
    openai_parser.add_argument("prompt_file", type=Path)
    openai_parser.add_argument("--model", required=True)
    openai_parser.add_argument("--base-url", default="https://api.openai.com/v1")
    openai_parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    openai_parser.add_argument("--timeout", type=float, default=120.0)
    openai_parser.add_argument("--max-retries", type=int, default=2)
    openai_parser.add_argument(
        "--candidate-count", type=int, choices=range(1, 6), default=1
    )
    openai_parser.add_argument("--domain")
    openai_parser.add_argument(
        "--mode",
        choices=["balanced", "maximum_quality", "concise"],
        default="maximum_quality",
    )
    openai_parser.add_argument(
        "--output-format",
        choices=["prompt_only", "standard", "evaluation_package"],
        default="standard",
    )
    openai_parser.add_argument("--artifact", type=Path)

    command_parser = subparsers.add_parser("optimize-command")
    command_parser.add_argument("prompt_file", type=Path)
    command_parser.add_argument("--permissions", type=Path, required=True)
    command_parser.add_argument("--model", default="external-command")
    command_parser.add_argument("--timeout", type=float, default=120.0)
    command_parser.add_argument(
        "--candidate-count", type=int, choices=range(1, 6), default=1
    )
    command_parser.add_argument("--domain")
    command_parser.add_argument(
        "--mode",
        choices=["balanced", "maximum_quality", "concise"],
        default="maximum_quality",
    )
    command_parser.add_argument(
        "--output-format",
        choices=["prompt_only", "standard", "evaluation_package"],
        default="standard",
    )
    command_parser.add_argument("--artifact", type=Path)
    command_parser.add_argument(
        "--command",
        dest="external_command",
        nargs=argparse.REMAINDER,
        required=True,
        help="External executable and arguments; this option must be last.",
    )

    codex_parser = subparsers.add_parser("optimize-codex")
    codex_parser.add_argument("prompt_file", type=Path)
    codex_parser.add_argument("--model", default="gpt-5.5")
    codex_parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        default="low",
    )
    codex_parser.add_argument("--timeout", type=float, default=300.0)
    codex_parser.add_argument(
        "--candidate-count", type=int, choices=range(1, 6), default=1
    )
    codex_parser.add_argument("--domain")
    codex_parser.add_argument(
        "--mode",
        choices=["balanced", "maximum_quality", "concise"],
        default="maximum_quality",
    )
    codex_parser.add_argument(
        "--output-format",
        choices=["prompt_only", "standard", "evaluation_package"],
        default="standard",
    )
    codex_parser.add_argument("--artifact", type=Path)

    profile_parser = subparsers.add_parser("resolve-domain")
    profile_parser.add_argument("prompt_file", type=Path)
    profile_parser.add_argument("--domain")

    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("prompt_file", type=Path)
    audit_parser.add_argument("--source", type=Path)

    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("paths", nargs="+", type=Path)
    manifest_parser.add_argument("--root", type=Path, default=Path.cwd())
    manifest_parser.add_argument("-o", "--output", type=Path, required=True)

    verify_manifest_parser = subparsers.add_parser("verify-manifest")
    verify_manifest_parser.add_argument("manifest", type=Path)
    verify_manifest_parser.add_argument("--root", type=Path, default=Path.cwd())

    benchmark_parser = subparsers.add_parser("validate-benchmark")
    benchmark_parser.add_argument("benchmark", type=Path)

    evaluate_parser = subparsers.add_parser("evaluate-recorded")
    evaluate_parser.add_argument("benchmark", type=Path)
    evaluate_parser.add_argument("job_id")
    evaluate_parser.add_argument("original_prompt", type=Path)
    evaluate_parser.add_argument("optimized_prompt", type=Path)
    evaluate_parser.add_argument("recorded_run", type=Path)
    evaluate_parser.add_argument("-o", "--output", type=Path, required=True)

    validate_evaluation_parser = subparsers.add_parser("validate-evaluation")
    validate_evaluation_parser.add_argument("evaluation", type=Path)

    code_evidence_parser = subparsers.add_parser("build-code-evidence")
    code_evidence_parser.add_argument("evaluation", type=Path)
    code_evidence_parser.add_argument("--report-id", required=True)
    code_evidence_parser.add_argument(
        "--sandbox-backend",
        choices=["host", "docker"],
        default="host",
    )
    code_evidence_parser.add_argument("--sandbox-image")
    code_evidence_parser.add_argument("-o", "--output", type=Path, required=True)

    review_packet_parser = subparsers.add_parser("create-review-packet")
    review_packet_parser.add_argument("evaluations", nargs="+", type=Path)
    review_packet_parser.add_argument("--reviewer", required=True)
    review_packet_parser.add_argument("--sample-size", type=int, default=24)
    review_packet_parser.add_argument("--seed", type=int, default=0)
    review_packet_parser.add_argument("--position-probes", type=int, default=2)
    review_packet_parser.add_argument("--packet", type=Path, required=True)
    review_packet_parser.add_argument("--key", type=Path, required=True)

    review_validate_parser = subparsers.add_parser("validate-review-submission")
    review_validate_parser.add_argument("packet", type=Path)
    review_validate_parser.add_argument("submission", type=Path)

    review_aggregate_parser = subparsers.add_parser("aggregate-human-review")
    review_aggregate_parser.add_argument("plan", type=Path)
    review_aggregate_parser.add_argument("-o", "--output", type=Path, required=True)

    image_manifest_parser = subparsers.add_parser(
        "register-image-generations"
    )
    image_manifest_parser.add_argument("plan", type=Path)
    image_manifest_parser.add_argument("-o", "--output", type=Path, required=True)

    image_packet_parser = subparsers.add_parser(
        "create-visual-review-packet"
    )
    image_packet_parser.add_argument("manifest", type=Path)
    image_packet_parser.add_argument("--reviewer", required=True)
    image_packet_parser.add_argument("--seed", type=int, default=0)
    image_packet_parser.add_argument("--packet", type=Path, required=True)
    image_packet_parser.add_argument("--key", type=Path, required=True)

    image_profile_parser = subparsers.add_parser(
        "create-visual-reviewer-profile"
    )
    image_profile_parser.add_argument("--reviewer", required=True)
    image_profile_parser.add_argument("--experience-years", type=int, required=True)
    image_profile_parser.add_argument(
        "--domain",
        action="append",
        dest="domains",
        required=True,
    )
    image_profile_parser.add_argument(
        "--independent",
        action="store_true",
        required=True,
    )
    image_profile_parser.add_argument(
        "--conflict-disclosed",
        action="store_true",
        required=True,
    )
    image_profile_parser.add_argument("-o", "--output", type=Path, required=True)

    image_validate_parser = subparsers.add_parser(
        "validate-visual-review-submission"
    )
    image_validate_parser.add_argument("packet", type=Path)
    image_validate_parser.add_argument("submission", type=Path)

    image_aggregate_parser = subparsers.add_parser(
        "aggregate-visual-review"
    )
    image_aggregate_parser.add_argument("plan", type=Path)
    image_aggregate_parser.add_argument("-o", "--output", type=Path, required=True)

    serve_openai_parser = subparsers.add_parser("serve-openai")
    serve_openai_parser.add_argument("--model", required=True)
    serve_openai_parser.add_argument("--base-url", default="https://api.openai.com/v1")
    serve_openai_parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    serve_openai_parser.add_argument("--timeout", type=float, default=120.0)
    serve_openai_parser.add_argument("--max-retries", type=int, default=2)
    serve_openai_parser.add_argument("--host", default="127.0.0.1")
    serve_openai_parser.add_argument("--port", type=int, default=8787)
    serve_openai_parser.add_argument("--db", type=Path, default=Path("data/jobs.sqlite3"))
    serve_openai_parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path("artifacts"),
    )
    serve_openai_parser.add_argument("--auth-token-env")

    serve_command_parser = subparsers.add_parser("serve-command")
    serve_command_parser.add_argument("--permissions", type=Path, required=True)
    serve_command_parser.add_argument("--model", default="external-command")
    serve_command_parser.add_argument("--timeout", type=float, default=120.0)
    serve_command_parser.add_argument("--host", default="127.0.0.1")
    serve_command_parser.add_argument("--port", type=int, default=8787)
    serve_command_parser.add_argument("--db", type=Path, default=Path("data/jobs.sqlite3"))
    serve_command_parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path("artifacts"),
    )
    serve_command_parser.add_argument("--auth-token-env")
    serve_command_parser.add_argument(
        "--command",
        dest="external_command",
        nargs=argparse.REMAINDER,
        required=True,
        help="External executable and arguments; this option must be last.",
    )

    migrate_prompt_parser = subparsers.add_parser("migrate-legacy-prompt")
    migrate_prompt_parser.add_argument("prompt_file", type=Path)
    migrate_prompt_parser.add_argument("--legacy-version", default="unknown")
    migrate_prompt_parser.add_argument("--domain")
    migrate_prompt_parser.add_argument("-o", "--output", type=Path, required=True)

    import_audit_parser = subparsers.add_parser("import-legacy-audit")
    import_audit_parser.add_argument("legacy_audit", type=Path)
    import_audit_parser.add_argument("-o", "--output", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate-artifact")
    validate_parser.add_argument("artifact", type=Path)

    readiness_parser = subparsers.add_parser("assess-readiness")
    readiness_parser.add_argument("manifest", type=Path)
    readiness_parser.add_argument("--root", type=Path)
    readiness_parser.add_argument("-o", "--output", type=Path)
    readiness_parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Return a failing exit code unless every stable-release gate passes.",
    )

    validate_readiness_parser = subparsers.add_parser("validate-readiness")
    validate_readiness_parser.add_argument("report", type=Path)
    return parser


def _load_tool_permissions(path: Path) -> ToolPermissionManifest:
    permission_data = json.loads(path.read_text(encoding="utf-8"))
    if permission_data.get("schema_version") != "1.0.0":
        raise ValueError("Unsupported tool permission manifest schema.")
    working_directory = permission_data.get("working_directory")
    return ToolPermissionManifest(
        allowed_executables=tuple(permission_data["allowed_executables"]),
        allowed_environment=tuple(
            permission_data.get("allowed_environment", [])
        ),
        working_directory=(
            (path.parent / working_directory).resolve()
            if working_directory is not None
            else None
        ),
        maximum_timeout_seconds=float(
            permission_data.get("maximum_timeout_seconds", 300)
        ),
        allow_sensitive_environment=bool(
            permission_data.get("allow_sensitive_environment", False)
        ),
    )


def _service_token(host: str, environment_name: str | None) -> str | None:
    token = (
        os.environ.get(environment_name, "").strip()
        if environment_name is not None
        else ""
    )
    loopback = host in {"127.0.0.1", "::1", "localhost"}
    if not loopback:
        raise ValueError(
            "Direct non-loopback binding is unsupported. Bind to loopback and use "
            "an authenticated TLS reverse proxy for remote access."
        )
    return token or None


def _run_service(
    *,
    adapter_factory,
    host: str,
    port: int,
    db: Path,
    artifacts: Path,
    auth_token_env: str | None,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service_token = _service_token(host, auth_token_env)
    service = OptimizationService(
        store=JobStore(db),
        artifacts=ArtifactStore(artifacts),
        adapter_factory=adapter_factory,
    )
    server = create_http_server(
        service,
        host=host,
        port=port,
        service_token=service_token,
    )
    print(
        json.dumps(
            {
                "status": "serving",
                "address": f"http://{host}:{server.server_address[1]}",
                "authentication": "bearer" if service_token else "local-only",
            },
            ensure_ascii=False,
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "compile":
        request = OptimizationRequest(
            source_prompt=args.prompt_file.read_text(encoding="utf-8"),
            domain=args.domain,
            mode=args.mode,
            output_format=args.output_format,
        )
        rendered = json.dumps(compile_request(request), ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 0

    if args.command == "optimize":
        request = OptimizationRequest(
            source_prompt=args.prompt_file.read_text(encoding="utf-8"),
            domain=args.domain,
            mode=args.mode,
            output_format=args.output_format,
        )
        responses = [
            path.read_text(encoding="utf-8") for path in args.mock_response
        ]
        result = optimize(
            request,
            MockSequenceAdapter(responses),
            candidate_count=args.candidate_count,
        )
        if args.artifact:
            _write_json(args.artifact, result.artifact)
        print(result.optimized_prompt)
        return 0

    if args.command == "optimize-openai":
        request = OptimizationRequest(
            source_prompt=args.prompt_file.read_text(encoding="utf-8"),
            domain=args.domain,
            mode=args.mode,
            output_format=args.output_format,
        )
        adapter = OpenAIResponsesAdapter(
            model=args.model,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
            retry_policy=RetryPolicy(max_retries=args.max_retries),
        )
        result = optimize(
            request,
            adapter,
            candidate_count=args.candidate_count,
        )
        if args.artifact:
            _write_json(args.artifact, result.artifact)
        print(result.optimized_prompt)
        return 0

    if args.command == "optimize-command":
        command = list(args.external_command)
        if not command:
            raise ValueError("An external command is required after --command.")
        permissions = _load_tool_permissions(args.permissions)
        request = OptimizationRequest(
            source_prompt=args.prompt_file.read_text(encoding="utf-8"),
            domain=args.domain,
            mode=args.mode,
            output_format=args.output_format,
        )
        result = optimize(
            request,
            ExternalCommandAdapter(
                command=tuple(command),
                permissions=permissions,
                model=args.model,
                timeout_seconds=args.timeout,
            ),
            candidate_count=args.candidate_count,
        )
        if args.artifact:
            _write_json(args.artifact, result.artifact)
        print(result.optimized_prompt)
        return 0

    if args.command == "optimize-codex":
        request = OptimizationRequest(
            source_prompt=args.prompt_file.read_text(encoding="utf-8"),
            domain=args.domain,
            mode=args.mode,
            output_format=args.output_format,
        )
        result = optimize(
            request,
            CodexExecAdapter(
                model=args.model,
                working_directory=Path.cwd(),
                reasoning_effort=args.reasoning_effort,
                timeout_seconds=args.timeout,
            ),
            candidate_count=args.candidate_count,
        )
        if args.artifact:
            _write_json(args.artifact, result.artifact)
        print(result.optimized_prompt)
        return 0

    if args.command == "serve-openai":
        return _run_service(
            adapter_factory=lambda: OpenAIResponsesAdapter(
                model=args.model,
                api_key_env=args.api_key_env,
                base_url=args.base_url,
                timeout_seconds=args.timeout,
                retry_policy=RetryPolicy(max_retries=args.max_retries),
            ),
            host=args.host,
            port=args.port,
            db=args.db,
            artifacts=args.artifacts,
            auth_token_env=args.auth_token_env,
        )

    if args.command == "serve-command":
        permissions = _load_tool_permissions(args.permissions)
        command = tuple(args.external_command)
        if not command:
            raise ValueError("An external command is required after --command.")
        return _run_service(
            adapter_factory=lambda: ExternalCommandAdapter(
                command=command,
                permissions=permissions,
                model=args.model,
                timeout_seconds=args.timeout,
            ),
            host=args.host,
            port=args.port,
            db=args.db,
            artifacts=args.artifacts,
            auth_token_env=args.auth_token_env,
        )

    if args.command == "migrate-legacy-prompt":
        package = migrate_legacy_prompt(
            args.prompt_file.read_text(encoding="utf-8"),
            legacy_version=args.legacy_version,
            domain=args.domain,
        )
        args.output.write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(package["migration_sha256"])
        return 0

    if args.command == "import-legacy-audit":
        package = import_legacy_audit(
            json.loads(args.legacy_audit.read_text(encoding="utf-8"))
        )
        args.output.write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(package["migration_sha256"])
        return 0

    if args.command == "resolve-domain":
        source = args.prompt_file.read_text(encoding="utf-8")
        profile = resolve_profile(source, args.domain, load_profiles())
        print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit":
        source = (
            args.source.read_text(encoding="utf-8")
            if args.source is not None
            else None
        )
        report = audit_prompt(
            args.prompt_file.read_text(encoding="utf-8"),
            source_prompt=source,
        )
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.passed else 1

    if args.command == "manifest":
        manifest = build_manifest(args.paths, root=args.root)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(manifest["manifest_sha256"])
        return 0

    if args.command == "verify-manifest":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        failures = verify_manifest(manifest, root=args.root)
        print(
            json.dumps(
                {"valid": not failures, "failures": failures},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    if args.command == "validate-benchmark":
        suite_id, jobs = load_benchmark_definition(args.benchmark)
        failures = validate_benchmark(suite_id, jobs)
        print(
            json.dumps(
                {
                    "valid": not failures,
                    "suite_id": suite_id,
                    "jobs": len(jobs),
                    "cases": sum(len(job.cases) for job in jobs),
                    "failures": failures,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    if args.command == "evaluate-recorded":
        suite_id, jobs = load_benchmark_definition(args.benchmark)
        matching = [job for job in jobs if job.job_id == args.job_id]
        if len(matching) != 1:
            raise ValueError(f"Unknown or duplicate benchmark job: {args.job_id}")
        job = matching[0]
        run = json.loads(args.recorded_run.read_text(encoding="utf-8"))
        if run.get("suite_id") != suite_id or run.get("job_id") != job.job_id:
            raise ValueError("Recorded run does not match benchmark suite and job.")
        original_prompt = args.original_prompt.read_text(encoding="utf-8")
        optimized_prompt = args.optimized_prompt.read_text(encoding="utf-8")
        original_hash = hashlib.sha256(original_prompt.encode("utf-8")).hexdigest()
        optimized_hash = hashlib.sha256(optimized_prompt.encode("utf-8")).hexdigest()
        outputs_by_case = {
            item["case_id"]: item
            for item in run.get("outputs", [])
        }
        recorded_outputs = {}
        for case in job.cases:
            item = outputs_by_case.get(case.case_id)
            if item is None:
                raise ValueError(f"Missing recorded outputs for {case.case_id}.")
            input_hash = hashlib.sha256(case.input_text.encode("utf-8")).hexdigest()
            recorded_outputs[(original_hash, input_hash)] = item["original"]
            recorded_outputs[(optimized_hash, input_hash)] = item["optimized"]
        judges = [
            RecordedJudge(
                [
                    JudgeDecision(
                        winner=decision["winner"],
                        reason=decision["reason"],
                        fatal_flaw_a=bool(decision.get("fatal_flaw_a", False)),
                        fatal_flaw_b=bool(decision.get("fatal_flaw_b", False)),
                    )
                    for decision in judge["decisions"]
                ],
                name=judge["name"],
            )
            for judge in run.get("judges", [])
        ]
        config = ExecutionConfig(**run["execution_config"])
        result = evaluate_suite(
            suite_id=f"{suite_id}:{job.job_id}",
            original_prompt=original_prompt,
            optimized_prompt=optimized_prompt,
            cases=job.cases,
            executor=RecordedExecutor(recorded_outputs),
            judges=judges,
            config=config,
            blind_seed=int(run.get("blind_seed", 0)),
        )
        failures = validate_evaluation(result)
        if failures:
            raise ValueError(f"Generated evaluation is invalid: {failures}")
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "wins": result["wins"],
                    "ties": result["ties"],
                    "losses": result["losses"],
                    "evidence": result["evidence"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "validate-evaluation":
        result = json.loads(args.evaluation.read_text(encoding="utf-8"))
        failures = validate_evaluation(result)
        print(
            json.dumps(
                {"valid": not failures, "failures": failures},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    if args.command == "create-review-packet":
        evaluations = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in args.evaluations
        ]
        packet, key = create_reviewer_packet(
            evaluations,
            reviewer_id=args.reviewer,
            sample_size=args.sample_size,
            seed=args.seed,
            position_probe_count=args.position_probes,
        )
        args.packet.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        args.key.write_text(
            json.dumps(key, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "packet": str(args.packet),
                    "key": str(args.key),
                    "items": len(packet["items"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "validate-review-submission":
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        submission = json.loads(args.submission.read_text(encoding="utf-8"))
        failures = validate_submission(packet, submission)
        print(
            json.dumps(
                {"valid": not failures, "failures": failures},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    if args.command == "aggregate-human-review":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if plan.get("schema_version") != "1.0.0":
            raise ValueError("Unsupported human-review plan schema.")
        root = args.plan.resolve().parent

        def load(relative: str) -> dict:
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Human-review path escapes plan root: {relative}") from exc
            return json.loads(path.read_text(encoding="utf-8"))

        evaluations = [load(path) for path in plan["evaluations"]]
        packets = [load(review["packet"]) for review in plan["reviews"]]
        keys = [load(review["key"]) for review in plan["reviews"]]
        submissions = [load(review["submission"]) for review in plan["reviews"]]
        report = aggregate_human_review(
            evaluations,
            packets,
            keys,
            submissions,
            adjudications=plan.get("adjudications", {}),
        )
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "reviewers": report["reviewer_count"],
                    "cases": report["reviewed_case_count"],
                    "evidence": report["evidence"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "register-image-generations":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        root = args.plan.resolve().parent
        if args.output.resolve().parent != root:
            raise ValueError(
                "Image generation manifest must be written beside its plan "
                "so relative asset paths remain stable."
            )
        manifest = build_generation_manifest(plan, root=root)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "cases": len(manifest["cases"]),
                    "actual_generation": manifest["actual_generation"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "create-visual-review-packet":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        packet, key = create_visual_review_packet(
            manifest,
            root=args.manifest.resolve().parent,
            reviewer_id=args.reviewer,
            seed=args.seed,
        )
        delivered = deliver_visual_review_assets(
            key,
            source_root=args.manifest.resolve().parent,
            packet_root=args.packet.resolve().parent,
        )
        for path, payload in ((args.packet, packet), (args.key, key)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(
            json.dumps(
                {
                    "packet": str(args.packet),
                    "key": str(args.key),
                    "items": len(packet["items"]),
                    "delivered_assets": len(delivered),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "create-visual-reviewer-profile":
        profile = build_reviewer_profile(
            args.reviewer,
            visual_review_experience_years=args.experience_years,
            relevant_domains=args.domains,
            independent=args.independent,
            conflict_disclosed=args.conflict_disclosed,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(profile["profile_sha256"])
        return 0

    if args.command == "validate-visual-review-submission":
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        submission = json.loads(args.submission.read_text(encoding="utf-8"))
        failures = validate_visual_submission(packet, submission)
        print(
            json.dumps(
                {"valid": not failures, "failures": failures},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    if args.command == "aggregate-visual-review":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if plan.get("schema_version") != "1.0.0":
            raise ValueError("Unsupported visual-review plan schema.")
        root = args.plan.resolve().parent

        def load_visual(relative: str) -> dict:
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"Visual-review path escapes plan root: {relative}"
                ) from exc
            return json.loads(path.read_text(encoding="utf-8"))

        reviews = plan.get("reviews", [])
        manifest = load_visual(plan["generation_manifest"])
        report = aggregate_visual_review(
            manifest,
            [load_visual(review["packet"]) for review in reviews],
            [load_visual(review["key"]) for review in reviews],
            [load_visual(review["submission"]) for review in reviews],
            [load_visual(review["profile"]) for review in reviews],
            root=root,
            report_id=str(plan["report_id"]),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "generated_cases": report["facts"]["generated_cases"],
                    "reviewed_cases": report["facts"]["reviewed_cases"],
                    "qualified_reviewers": report["facts"]["qualified_reviewers"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "validate-artifact":
        data = json.loads(args.artifact.read_text(encoding="utf-8"))
        violations = validate_artifact(data)
        report = {
            "valid": not violations,
            "violations": [asdict(item) for item in violations],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if violations else 0

    if args.command == "build-code-evidence":
        evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
        if args.sandbox_backend == "docker" and not args.sandbox_image:
            raise ValueError("--sandbox-image is required for Docker sandboxing.")
        sandbox = (
            DockerSandbox(args.sandbox_image)
            if args.sandbox_backend == "docker"
            else None
        )
        report = build_code_execution_evidence(
            evaluation,
            report_id=args.report_id,
            sandbox=sandbox,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "eligible_cases": report["facts"]["eligible_cases"],
                    "executed_cases": report["facts"]["executed_cases"],
                    "passed_cases": report["facts"]["passed_cases"],
                    "sandboxed": report["facts"]["sandboxed"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "assess-readiness":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        root = (
            args.root.resolve()
            if args.root is not None
            else args.manifest.resolve().parent
        )
        report = assess_readiness(manifest, root=root)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output is not None:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return int(args.require_complete and report["status"] != "complete")

    if args.command == "validate-readiness":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        failures = validate_readiness_report(report)
        print(
            json.dumps(
                {"valid": not failures, "failures": failures},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    raise AssertionError(f"Unhandled command: {args.command}")
