from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import prompt_performance_engine.frontier_io as frontier_io
from prompt_performance_engine.frontier_io import (
    ArtifactConflictError,
    ArtifactFormatError,
    ArtifactPathError,
    FrontierArtifactIOError,
    MAX_SAFE_JSON_INTEGER,
    MIN_SAFE_JSON_INTEGER,
    canonical_authority_bytes,
    canonical_authority_sha256,
    load_authority_artifact,
    load_canonical_authority_bytes,
    load_contained_file,
    write_authority_artifact,
)
from prompt_performance_engine.hashing import canonical_json_bytes


class CanonicalAuthorityBytesTests(unittest.TestCase):
    def test_utf8_sorted_compact_and_exactly_one_lf(self) -> None:
        value = {"z": True, "é": [1, "雪"], "a": None}
        raw = canonical_authority_bytes(value)

        self.assertEqual(raw, '{"a":null,"z":true,"é":[1,"雪"]}\n'.encode())
        self.assertTrue(raw.endswith(b"\n"))
        self.assertFalse(raw.endswith(b"\n\n"))

    def test_hash_is_of_the_exact_written_bytes(self) -> None:
        value = {"artifact_kind": "frontier_report", "version": 1}
        raw = canonical_authority_bytes(value)

        self.assertEqual(
            canonical_authority_sha256(value),
            hashlib.sha256(raw).hexdigest(),
        )

    def test_bool_is_preserved_as_json_bool_not_integer(self) -> None:
        self.assertEqual(canonical_authority_bytes({"passed": True}), b'{"passed":true}\n')

    def test_integers_are_limited_to_the_cross_language_safe_range(self) -> None:
        raw = canonical_authority_bytes(
            {"maximum": MAX_SAFE_JSON_INTEGER, "minimum": MIN_SAFE_JSON_INTEGER}
        )
        self.assertEqual(load_canonical_authority_bytes(raw).value["maximum"], 2**53 - 1)
        for value in (MIN_SAFE_JSON_INTEGER - 1, MAX_SAFE_JSON_INTEGER + 1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ArtifactFormatError, "safe range"):
                    canonical_authority_bytes({"integer": value})
                with self.assertRaisesRegex(ArtifactFormatError, "safe range"):
                    load_canonical_authority_bytes(f'{{"integer":{value}}}\n'.encode())

    def test_nonfinite_numbers_are_rejected_everywhere(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ArtifactFormatError):
                    canonical_authority_bytes({"metric": value})
                with self.assertRaises(ValueError):
                    canonical_json_bytes({"metric": value})

    def test_non_json_types_and_non_string_keys_are_rejected(self) -> None:
        for value in ({"x": (1, 2)}, {1: "x"}, {"x": object()}):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ArtifactFormatError):
                    canonical_authority_bytes(value)  # type: ignore[arg-type]

    def test_root_must_be_object(self) -> None:
        with self.assertRaises(ArtifactFormatError):
            canonical_authority_bytes([1, 2])  # type: ignore[arg-type]

    def test_cycles_and_excessive_depth_are_rejected(self) -> None:
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with self.assertRaisesRegex(ArtifactFormatError, "cycle"):
            canonical_authority_bytes(cyclic)

        value: object = "leaf"
        for _ in range(10):
            value = [value]
        with self.assertRaisesRegex(ArtifactFormatError, "depth"):
            canonical_authority_bytes({"x": value}, max_depth=5)

    def test_bool_limits_are_not_accepted_as_integers(self) -> None:
        with self.assertRaises(FrontierArtifactIOError):
            canonical_authority_bytes({}, max_depth=True)  # type: ignore[arg-type]
        with self.assertRaises(FrontierArtifactIOError):
            load_canonical_authority_bytes(b"{}\n", max_bytes=True)  # type: ignore[arg-type]


class StrictLoadTests(unittest.TestCase):
    def test_round_trip_retains_the_exact_bytes(self) -> None:
        raw = canonical_authority_bytes({"b": 2, "a": "雪"})
        loaded = load_canonical_authority_bytes(raw)

        self.assertEqual(loaded.value, {"a": "雪", "b": 2})
        self.assertIs(loaded.canonical_bytes, raw)
        self.assertEqual(loaded.content_sha256, hashlib.sha256(raw).hexdigest())

    def test_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaises(ArtifactFormatError):
            load_canonical_authority_bytes(b'{"a":1,"a":2}\n')

    def test_non_utf8_is_rejected_without_echoing_content(self) -> None:
        secret = b"SUPER_SECRET_\xff"
        with self.assertRaises(ArtifactFormatError) as caught:
            load_canonical_authority_bytes(b'{"x":"' + secret + b'"}\n')
        self.assertNotIn("SUPER_SECRET", str(caught.exception))

    def test_nan_infinity_and_float_overflow_are_rejected(self) -> None:
        for raw in (b'{"x":NaN}\n', b'{"x":Infinity}\n', b'{"x":1e999}\n'):
            with self.subTest(raw=raw):
                with self.assertRaises(ArtifactFormatError):
                    load_canonical_authority_bytes(raw)

    def test_noncanonical_input_is_not_silently_normalized(self) -> None:
        invalid = (
            b'{"b":2,"a":1}\n',
            b'{"a":1, "b":2}\n',
            b'{"a":1,"b":2}',
            b'{"a":1,"b":2}\n\n',
            b'\xef\xbb\xbf{"a":1,"b":2}\n',
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(ArtifactFormatError):
                    load_canonical_authority_bytes(raw)

    def test_scalar_and_array_roots_are_rejected(self) -> None:
        for raw in (b"null\n", b"[]\n", b"true\n"):
            with self.subTest(raw=raw):
                with self.assertRaises(ArtifactFormatError):
                    load_canonical_authority_bytes(raw)


class AuthorityFileIOTests(unittest.TestCase):
    def test_atomic_write_load_idempotence_and_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = write_authority_artifact(root, "report.json", {"v": 1})
            second = write_authority_artifact(root, "report.json", {"v": 1})

            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.content_sha256, second.content_sha256)
            self.assertEqual(root.joinpath("report.json").read_bytes(), b'{"v":1}\n')
            self.assertEqual(load_authority_artifact(root, "report.json").value, {"v": 1})
            with self.assertRaises(ArtifactConflictError):
                write_authority_artifact(root, "report.json", {"v": 2})
            self.assertEqual(root.joinpath("report.json").read_bytes(), b'{"v":1}\n')

    def test_nested_existing_directory_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath("campaign", "evidence").mkdir(parents=True)
            result = write_authority_artifact(
                root,
                "campaign/evidence/report.json",
                {"ok": True},
            )
            self.assertEqual(result.relative_path, "campaign/evidence/report.json")

    def test_parent_directories_are_not_implicitly_created(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ArtifactPathError):
                write_authority_artifact(
                    Path(temporary),
                    "missing/report.json",
                    {"ok": True},
                )

    def test_absolute_and_traversal_paths_are_rejected_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = "SECRET_PATH_COMPONENT"
            candidates = [
                root.joinpath(secret, "report.json"),
                Path("..") / secret / "report.json",
                Path(".") / ".." / secret,
            ]
            for candidate in candidates:
                with self.subTest(candidate=str(candidate)):
                    with self.assertRaises(ArtifactPathError) as caught:
                        write_authority_artifact(root, candidate, {"ok": True})
                    self.assertNotIn(secret, str(caught.exception))
                    self.assertNotIn(str(root), str(caught.exception))

    def test_ambiguous_windows_names_and_alternate_streams_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for candidate in (
                "report.json:stream",
                "report.json.",
                "report.json ",
                "NUL.json",
                "aux",
                "COM1.txt",
                "COM9.txt",
                "COM¹.txt",
                "LPT1.log",
                "LPT³.log",
                "lpt9",
                "CONIN$",
                "conout$.json",
            ):
                with self.subTest(candidate=candidate):
                    with self.assertRaises(ArtifactPathError):
                        write_authority_artifact(root, candidate, {"ok": True})

    def test_non_nfc_paths_are_rejected_without_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            decomposed = "caf\u0065\u0301.json"
            composed = "caf\u00e9.json"

            with self.assertRaises(ArtifactPathError):
                write_authority_artifact(root, decomposed, {"ok": True})
            written = write_authority_artifact(root, composed, {"ok": True})
            self.assertEqual(written.relative_path, composed)

    def test_backslashes_are_rejected_as_nonportable_on_every_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for candidate in (
                r"safe\report.json",
                r"..\SECRET\report.json",
                r"\\server\share\report.json",
            ):
                with self.subTest(candidate=candidate):
                    with self.assertRaises(ArtifactPathError) as caught:
                        write_authority_artifact(root, candidate, {"ok": True})
                    self.assertNotIn("SECRET", str(caught.exception))

    def test_noncanonical_file_is_rejected_not_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root.joinpath("report.json")
            target.write_bytes(b'{"b":2, "a":1}\n')
            with self.assertRaises(ArtifactFormatError):
                load_authority_artifact(root, "report.json")
            self.assertEqual(target.read_bytes(), b'{"b":2, "a":1}\n')

    def test_directory_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath("report.json").mkdir()
            with self.assertRaises(ArtifactPathError):
                load_authority_artifact(root, "report.json")
            with self.assertRaises(ArtifactPathError):
                write_authority_artifact(root, "report.json", {"ok": True})

    def test_symlink_parent_and_target_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            outside_root = Path(outside)
            outside_root.joinpath("report.json").write_bytes(b'{"outside":true}\n')
            try:
                root.joinpath("linked-dir").symlink_to(outside_root, target_is_directory=True)
                root.joinpath("linked-file.json").symlink_to(
                    outside_root.joinpath("report.json")
                )
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symbolic links unavailable: {type(error).__name__}")

            with self.assertRaises(ArtifactPathError):
                write_authority_artifact(
                    root,
                    "linked-dir/new.json",
                    {"inside": True},
                )
            with self.assertRaises(ArtifactPathError):
                load_authority_artifact(root, "linked-file.json")
            self.assertFalse(outside_root.joinpath("new.json").exists())

    def test_symlink_root_is_not_silently_promoted_to_a_trust_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as parent:
            real_root = Path(temporary)
            real_root.joinpath("report.json").write_bytes(b'{"ok":true}\n')
            linked_root = Path(parent) / "linked-root"
            try:
                linked_root.symlink_to(real_root, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symbolic links unavailable: {type(error).__name__}")

            with self.assertRaises(ArtifactPathError):
                load_authority_artifact(linked_root, "report.json")

    def test_concurrent_same_byte_writers_publish_one_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            barrier = threading.Barrier(12)
            results = []
            failures = []

            def writer() -> None:
                try:
                    barrier.wait()
                    results.append(
                        write_authority_artifact(root, "report.json", {"x": "雪" * 1000})
                    )
                except BaseException as error:  # captured and asserted in main thread
                    failures.append(error)

            threads = [threading.Thread(target=writer) for _ in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(failures, [])
            self.assertEqual(sum(result.created for result in results), 1)
            loaded = load_authority_artifact(root, "report.json")
            self.assertEqual(loaded.value, {"x": "雪" * 1000})

    def test_conflicting_publish_race_never_overwrites_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            winning = canonical_authority_bytes({"winner": True})

            def competing_link(_source: object, target: object, **_kwargs: object) -> None:
                Path(target).write_bytes(winning)
                raise FileExistsError

            with patch(
                "prompt_performance_engine.frontier_io.os.link",
                side_effect=competing_link,
            ):
                with self.assertRaises(ArtifactConflictError):
                    write_authority_artifact(root, "report.json", {"winner": False})
            self.assertEqual(root.joinpath("report.json").read_bytes(), winning)

    def test_post_publish_validation_failure_rolls_back_owned_inode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root.joinpath("report.json")
            original = frontier_io._contained_target
            calls = 0

            def fail_second_call(*args: object, **kwargs: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ArtifactPathError("sanitized revalidation failure")
                return original(*args, **kwargs)

            with patch(
                "prompt_performance_engine.frontier_io._contained_target",
                side_effect=fail_second_call,
            ):
                with self.assertRaises(ArtifactPathError):
                    write_authority_artifact(root, "report.json", {"owned": True})

            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".frontier-artifact-*.tmp")), [])

    def test_rollback_does_not_delete_a_replacement_inode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root.joinpath("report.json")
            replacement = b'{"replacement":true}\n'
            original = frontier_io._contained_target
            calls = 0

            def replace_then_fail(*args: object, **kwargs: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 2:
                    staged = list(root.glob(".frontier-artifact-*.tmp"))
                    self.assertEqual(len(staged), 1)
                    self.assertEqual(
                        frontier_io._identity(staged[0].lstat()),
                        frontier_io._identity(target.lstat()),
                    )
                    target.unlink()
                    target.write_bytes(replacement)
                    raise ArtifactPathError("sanitized revalidation failure")
                return original(*args, **kwargs)

            with patch(
                "prompt_performance_engine.frontier_io._contained_target",
                side_effect=replace_then_fail,
            ):
                with self.assertRaises(ArtifactPathError):
                    write_authority_artifact(root, "report.json", {"owned": True})

            self.assertEqual(target.read_bytes(), replacement)
            self.assertEqual(list(root.glob(".frontier-artifact-*.tmp")), [])

    def test_byte_limit_is_enforced_for_write_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ArtifactFormatError):
                write_authority_artifact(root, "large.json", {"x": "12345"}, max_bytes=5)
            root.joinpath("large.json").write_bytes(b'{"x":"12345"}\n')
            with self.assertRaises(ArtifactFormatError):
                load_authority_artifact(root, "large.json", max_bytes=5)

    def test_bounded_contained_raw_read_returns_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath("payload.bin").write_bytes(b"raw-not-json")

            loaded = load_contained_file(root, "payload.bin", max_bytes=12)
            self.assertEqual(loaded.exact_bytes, b"raw-not-json")
            self.assertEqual(
                loaded.content_sha256,
                hashlib.sha256(b"raw-not-json").hexdigest(),
            )
            with self.assertRaises(ArtifactFormatError):
                load_contained_file(root, "payload.bin", max_bytes=11)

    def test_contained_read_revalidates_the_path_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath("nested").mkdir()
            target = root.joinpath("nested", "payload.bin")
            target.write_bytes(b"original")
            original = frontier_io._read_regular_file

            def replace_parent(path: Path, *, max_bytes: int) -> bytes:
                raw = original(path, max_bytes=max_bytes)
                root.joinpath("nested").rename(root.joinpath("old-nested"))
                root.joinpath("nested").mkdir()
                root.joinpath("nested", "payload.bin").write_bytes(b"replacement")
                return raw

            with patch(
                "prompt_performance_engine.frontier_io._read_regular_file",
                side_effect=replace_parent,
            ):
                with self.assertRaisesRegex(ArtifactPathError, "changed"):
                    load_contained_file(root, "nested/payload.bin")


if __name__ == "__main__":
    unittest.main()
