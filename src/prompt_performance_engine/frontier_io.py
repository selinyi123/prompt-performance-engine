"""Canonical, contained, write-once I/O for frontier authority artifacts.

This module intentionally treats the byte representation as part of the
authority contract.  Loading never normalizes a file: a JSON object is
accepted only when the bytes on disk are already the canonical bytes that
would be written by :func:`canonical_authority_bytes`.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from typing import Any


DEFAULT_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_JSON_DEPTH = 128
DEFAULT_MAX_JSON_NODES = 1_000_000
MIN_SAFE_JSON_INTEGER = -(2**53 - 1)
MAX_SAFE_JSON_INTEGER = 2**53 - 1


class FrontierArtifactIOError(ValueError):
    """Base class for sanitized frontier artifact I/O failures."""


class ArtifactPathError(FrontierArtifactIOError):
    """Raised when an artifact path is unsafe or not contained."""


class ArtifactFormatError(FrontierArtifactIOError):
    """Raised when artifact bytes are invalid or non-canonical."""


class ArtifactConflictError(FrontierArtifactIOError):
    """Raised when write-once storage already contains different bytes."""


@dataclass(frozen=True, slots=True)
class LoadedAuthorityArtifact:
    """A canonical authority artifact and the exact bytes that bound it."""

    value: dict[str, Any]
    canonical_bytes: bytes
    content_sha256: str


@dataclass(frozen=True, slots=True)
class LoadedContainedFile:
    """Exact bytes from a bounded, contained, stable regular file read."""

    exact_bytes: bytes
    content_sha256: str


@dataclass(frozen=True, slots=True)
class AuthorityArtifactWrite:
    """The non-sensitive outcome of an atomic authority artifact write."""

    relative_path: str
    content_sha256: str
    size_bytes: int
    created: bool


class _DuplicateKeyError(ValueError):
    pass


def _require_limit(value: int, *, default_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise FrontierArtifactIOError(f"{default_name} must be a positive integer")
    return value


def _validate_json_value(
    value: Any,
    *,
    max_depth: int,
    max_nodes: int,
) -> None:
    """Reject lossy JSON coercions, non-finite numbers, cycles, and deep trees."""

    max_depth = _require_limit(max_depth, default_name="max JSON depth")
    max_nodes = _require_limit(max_nodes, default_name="max JSON nodes")
    if type(value) is not dict:
        raise ArtifactFormatError("authority artifact root must be a JSON object")

    stack: list[tuple[Any, int, bool]] = [(value, 1, False)]
    active: set[int] = set()
    nodes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue

        nodes += 1
        if nodes > max_nodes:
            raise ArtifactFormatError("authority artifact exceeds the JSON node limit")
        if depth > max_depth:
            raise ArtifactFormatError("authority artifact exceeds the JSON depth limit")

        current_type = type(current)
        if current_type is dict:
            identity = id(current)
            if identity in active:
                raise ArtifactFormatError("authority artifact contains a JSON cycle")
            active.add(identity)
            stack.append((current, depth, True))
            for key, child in reversed(list(current.items())):
                if type(key) is not str:
                    raise ArtifactFormatError(
                        "authority artifact object keys must be strings"
                    )
                stack.append((child, depth + 1, False))
        elif current_type is list:
            identity = id(current)
            if identity in active:
                raise ArtifactFormatError("authority artifact contains a JSON cycle")
            active.add(identity)
            stack.append((current, depth, True))
            for child in reversed(current):
                stack.append((child, depth + 1, False))
        elif current_type is float:
            if not math.isfinite(current):
                raise ArtifactFormatError(
                    "authority artifact contains a non-finite number"
                )
        elif current_type is int:
            if current < MIN_SAFE_JSON_INTEGER or current > MAX_SAFE_JSON_INTEGER:
                raise ArtifactFormatError(
                    "authority artifact integer exceeds the portable safe range"
                )
        elif current_type in (str, bool) or current is None:
            continue
        else:
            raise ArtifactFormatError("authority artifact contains a non-JSON value")


def canonical_authority_bytes(
    value: dict[str, Any],
    *,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_nodes: int = DEFAULT_MAX_JSON_NODES,
) -> bytes:
    """Return canonical UTF-8 JSON object bytes with exactly one trailing LF."""

    _validate_json_value(value, max_depth=max_depth, max_nodes=max_nodes)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise ArtifactFormatError("authority artifact cannot be canonicalized") from None
    return encoded + b"\n"


def canonical_authority_sha256(
    value: dict[str, Any],
    *,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_nodes: int = DEFAULT_MAX_JSON_NODES,
) -> str:
    """Hash exactly the bytes returned by :func:`canonical_authority_bytes`."""

    return hashlib.sha256(
        canonical_authority_bytes(
            value,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
    ).hexdigest()


def _reject_constant(_value: str) -> float:
    raise ArtifactFormatError("authority artifact contains a non-finite number")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ArtifactFormatError("authority artifact contains a non-finite number")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def load_canonical_authority_bytes(
    data: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_nodes: int = DEFAULT_MAX_JSON_NODES,
) -> LoadedAuthorityArtifact:
    """Strictly parse canonical artifact bytes without silently normalizing them."""

    max_bytes = _require_limit(max_bytes, default_name="max artifact bytes")
    if type(data) is not bytes:
        raise ArtifactFormatError("authority artifact input must be bytes")
    if len(data) > max_bytes:
        raise ArtifactFormatError("authority artifact exceeds the byte limit")
    try:
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_parse_finite_float,
        )
    except (
        ArtifactFormatError,
        _DuplicateKeyError,
        json.JSONDecodeError,
        RecursionError,
        UnicodeError,
        ValueError,
    ):
        raise ArtifactFormatError("authority artifact is not strict JSON") from None
    _validate_json_value(value, max_depth=max_depth, max_nodes=max_nodes)
    canonical = canonical_authority_bytes(
        value,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    if data != canonical:
        raise ArtifactFormatError("authority artifact is not canonical JSON")
    return LoadedAuthorityArtifact(
        value=value,
        canonical_bytes=data,
        content_sha256=hashlib.sha256(data).hexdigest(),
    )


def _safe_relative_path(relative_path: str | os.PathLike[str]) -> Path:
    if isinstance(relative_path, bool):
        raise ArtifactPathError("artifact output path is invalid")
    try:
        raw = os.fspath(relative_path)
    except TypeError:
        raise ArtifactPathError("artifact output path is invalid") from None
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or "\\" in raw
        or unicodedata.normalize("NFC", raw) != raw
    ):
        raise ArtifactPathError("artifact output path is invalid")
    candidate = Path(raw)
    parts = candidate.parts
    if (
        candidate.is_absolute()
        or bool(candidate.drive)
        or bool(candidate.root)
        or candidate == Path(".")
        or any(part in ("", ".", "..") for part in parts)
        # Colons can select NTFS alternate data streams.  Trailing dots and
        # spaces are normalized by common Win32 paths and can alias another
        # name.  Reject them on every platform so a campaign has one portable
        # path identity.
        or any(
            ":" in part
            or part.endswith((".", " "))
            or any(ord(character) < 32 for character in part)
            for part in parts
        )
    ):
        raise ArtifactPathError("artifact output path must be contained and relative")
    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        # Win32 also reserves the ISO-8859-1 superscript digits 1, 2, and 3.
        *(f"COM{suffix}" for suffix in "123456789¹²³"),
        *(f"LPT{suffix}" for suffix in "123456789¹²³"),
    }
    for part in parts:
        stem = part.rstrip(" .").split(".", 1)[0].upper()
        if stem in reserved_names:
            raise ArtifactPathError("artifact output path is not portable")
    return candidate


def _resolved_root(root: str | os.PathLike[str]) -> Path:
    try:
        supplied = Path(root)
        supplied_info = supplied.lstat()
        if _is_link_like(supplied_info):
            raise ArtifactPathError("artifact root is unavailable")
        resolved = supplied.resolve(strict=True)
        info = resolved.stat()
    except FrontierArtifactIOError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ArtifactPathError("artifact root is unavailable") from None
    if not stat.S_ISDIR(info.st_mode):
        raise ArtifactPathError("artifact root is unavailable")
    return resolved


def _contained_target(
    root: str | os.PathLike[str],
    relative_path: str | os.PathLike[str],
) -> tuple[Path, Path, Path]:
    resolved_root = _resolved_root(root)
    relative = _safe_relative_path(relative_path)
    target = resolved_root.joinpath(relative)
    try:
        resolved_target = target.resolve(strict=False)
        resolved_target.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        raise ArtifactPathError("artifact output path escapes its root") from None

    # Reject every existing symlink below the resolved trust root.  This is
    # stricter than merely checking where a symlink currently resolves and
    # narrows the window for link-swap attacks.
    cursor = resolved_root
    for part in relative.parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise ArtifactPathError("artifact output path is unavailable") from None
        if _is_link_like(info):
            raise ArtifactPathError("artifact output path contains a symbolic link")
    return resolved_root, relative, target


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _is_link_like(info: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    max_bytes = _require_limit(max_bytes, default_name="max artifact bytes")
    descriptor: int | None = None
    try:
        before = path.lstat()
        if _is_link_like(before) or not stat.S_ISREG(before.st_mode):
            raise ArtifactPathError("artifact path is not a regular file")
        descriptor = os.open(path, _open_flags())
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
            raise ArtifactPathError("artifact path changed while being opened")
        if opened.st_size > max_bytes:
            raise ArtifactFormatError("authority artifact exceeds the byte limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after_open = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            len(raw) > max_bytes
            or _identity(opened) != _identity(after_open)
            or _identity(opened) != _identity(after_path)
            or after_open.st_size != len(raw)
            or after_open.st_size != opened.st_size
            or after_open.st_mtime_ns != opened.st_mtime_ns
        ):
            raise ArtifactPathError("artifact changed while being read")
        return raw
    except FrontierArtifactIOError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ArtifactPathError("artifact could not be read safely") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _path_chain_identities(root: Path, relative: Path) -> tuple[tuple[int, int], ...]:
    """Snapshot every existing path component without following link-like entries."""

    identities: list[tuple[int, int]] = []
    cursor = root
    try:
        root_info = cursor.lstat()
        if _is_link_like(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise ArtifactPathError("artifact root is unavailable")
        identities.append(_identity(root_info))
        for index, part in enumerate(relative.parts):
            cursor = cursor / part
            info = cursor.lstat()
            if _is_link_like(info):
                raise ArtifactPathError("artifact path contains a symbolic link")
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise ArtifactPathError("artifact path parent is unavailable")
            identities.append(_identity(info))
    except FrontierArtifactIOError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ArtifactPathError("artifact path is unavailable") from None
    return tuple(identities)


def load_contained_file(
    root: str | os.PathLike[str],
    relative_path: str | os.PathLike[str],
    *,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> LoadedContainedFile:
    """Read exact bytes from one contained regular file with stable path identity.

    The path is validated both before and after the bounded descriptor read.  A
    symlink, junction/reparse point, file replacement, or ordinary parent swap
    therefore fails closed instead of silently changing the authority source.
    """

    resolved_root, relative, target = _contained_target(root, relative_path)
    before = _path_chain_identities(resolved_root, relative)
    raw = _read_regular_file(target, max_bytes=max_bytes)
    after_root, after_relative, after_target = _contained_target(root, relative_path)
    after = _path_chain_identities(after_root, after_relative)
    if (
        after_root != resolved_root
        or after_relative != relative
        or after_target != target
        or after != before
    ):
        raise ArtifactPathError("artifact path changed while being read")
    return LoadedContainedFile(
        exact_bytes=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )


def resolve_contained_directory(
    root: str | os.PathLike[str],
    relative_path: str | os.PathLike[str],
) -> Path:
    """Resolve one existing contained directory without accepting path aliases."""

    resolved_root, relative, target = _contained_target(root, relative_path)
    before = _path_chain_identities(resolved_root, relative)
    try:
        info = target.lstat()
    except (OSError, RuntimeError, ValueError):
        raise ArtifactPathError("artifact directory is unavailable") from None
    if _is_link_like(info) or not stat.S_ISDIR(info.st_mode):
        raise ArtifactPathError("artifact directory is unavailable")
    after_root, after_relative, after_target = _contained_target(root, relative_path)
    after = _path_chain_identities(after_root, after_relative)
    if (
        after_root != resolved_root
        or after_relative != relative
        or after_target != target
        or after != before
    ):
        raise ArtifactPathError("artifact directory changed while being resolved")
    return target


def load_authority_artifact(
    root: str | os.PathLike[str],
    relative_path: str | os.PathLike[str],
    *,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_nodes: int = DEFAULT_MAX_JSON_NODES,
) -> LoadedAuthorityArtifact:
    """Load a contained file only when its existing bytes are canonical."""

    loaded = load_contained_file(root, relative_path, max_bytes=max_bytes)
    return load_canonical_authority_bytes(
        loaded.exact_bytes,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # File data is already fsynced.  Directory fsync is a durability
        # improvement on supporting platforms, not a portability requirement.
        return
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _safe_unlink_owned(path: Path, expected_identity: tuple[int, int]) -> bool:
    try:
        current = path.lstat()
        if _identity(current) == expected_identity and stat.S_ISREG(current.st_mode):
            path.unlink()
            return True
    except OSError:
        pass
    return False


def _rollback_owned_publication(
    target: Path,
    parent: Path,
    expected_identity: tuple[int, int],
) -> None:
    """Remove only the link that still names the staged publication inode."""

    if _safe_unlink_owned(target, expected_identity):
        _fsync_directory(parent)


def _write_temp(parent: Path, data: bytes) -> tuple[Path, tuple[int, int]]:
    descriptor: int | None = None
    temp_path: Path | None = None
    temp_identity: tuple[int, int] | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".frontier-artifact-",
            suffix=".tmp",
            dir=parent,
        )
        temp_path = Path(raw_path)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError(errno.EIO, "invalid staged file")
        temp_identity = _identity(opened)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            offset += written
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        if _identity(completed) != temp_identity or completed.st_size != len(data):
            raise OSError(errno.EIO, "unstable staged file")
        os.close(descriptor)
        descriptor = None
        staged = temp_path.lstat()
        if (
            not stat.S_ISREG(staged.st_mode)
            or staged.st_size != len(data)
            or _identity(staged) != temp_identity
        ):
            raise OSError(errno.EIO, "unstable staged file")
        return temp_path, temp_identity
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temp_path is not None and temp_identity is not None:
            _safe_unlink_owned(temp_path, temp_identity)
        raise FrontierArtifactIOError("authority artifact could not be staged") from None


def _existing_matches(target: Path, expected: bytes, *, max_bytes: int) -> bool:
    return _read_regular_file(target, max_bytes=max_bytes) == expected


def write_authority_artifact(
    root: str | os.PathLike[str],
    relative_path: str | os.PathLike[str],
    value: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_nodes: int = DEFAULT_MAX_JSON_NODES,
) -> AuthorityArtifactWrite:
    """Atomically create a canonical artifact with write-once semantics.

    Repeating a write with exactly the same canonical bytes is idempotent.
    Existing different bytes are never replaced.
    """

    max_bytes = _require_limit(max_bytes, default_name="max artifact bytes")
    data = canonical_authority_bytes(
        value,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    if len(data) > max_bytes:
        raise ArtifactFormatError("authority artifact exceeds the byte limit")
    resolved_root, relative, target = _contained_target(root, relative_path)
    parent = target.parent
    try:
        parent_info = parent.lstat()
    except OSError:
        raise ArtifactPathError("artifact output parent is unavailable") from None
    if _is_link_like(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
        raise ArtifactPathError("artifact output parent is unavailable")
    try:
        parent.resolve(strict=True).relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        raise ArtifactPathError("artifact output parent escapes its root") from None

    digest = hashlib.sha256(data).hexdigest()
    try:
        if target.exists() or target.is_symlink():
            if _existing_matches(target, data, max_bytes=max_bytes):
                return AuthorityArtifactWrite(
                    relative_path=relative.as_posix(),
                    content_sha256=digest,
                    size_bytes=len(data),
                    created=False,
                )
            raise ArtifactConflictError(
                "authority artifact already exists with different bytes"
            )
    except FrontierArtifactIOError:
        raise
    except OSError:
        raise ArtifactPathError("artifact output path is unavailable") from None

    temp_path, temp_identity = _write_temp(parent, data)
    created = False
    try:
        current_parent = parent.lstat()
        if _is_link_like(current_parent) or _identity(current_parent) != _identity(
            parent_info
        ):
            raise ArtifactPathError(
                "artifact output parent changed during publication"
            )
        current_temp = temp_path.lstat()
        if (
            _is_link_like(current_temp)
            or not stat.S_ISREG(current_temp.st_mode)
            or _identity(current_temp) != temp_identity
        ):
            raise ArtifactPathError("artifact staging file changed during publication")
        # A hard link publishes the fully-written temporary inode only if the
        # destination name is absent.  Unlike replace/rename, it cannot silently
        # overwrite a concurrent writer on either POSIX or Windows.
        os.link(temp_path, target, follow_symlinks=False)
        created = True
        published = target.lstat()
        if _is_link_like(published) or _identity(published) != temp_identity:
            raise ArtifactPathError("artifact publication identity is invalid")
        current_parent = parent.lstat()
        if _is_link_like(current_parent) or _identity(current_parent) != _identity(
            parent_info
        ):
            raise ArtifactPathError(
                "artifact output parent changed during publication"
            )
        _fsync_directory(parent)
    except FileExistsError:
        if not _existing_matches(target, data, max_bytes=max_bytes):
            raise ArtifactConflictError(
                "authority artifact already exists with different bytes"
            ) from None
    except FrontierArtifactIOError:
        if created:
            _rollback_owned_publication(target, parent, temp_identity)
        raise
    except OSError:
        if created:
            _rollback_owned_publication(target, parent, temp_identity)
        raise FrontierArtifactIOError(
            "authority artifact could not be published atomically"
        ) from None
    finally:
        _safe_unlink_owned(temp_path, temp_identity)

    # Revalidate containment and bytes after publication.  This cannot make a
    # hostile mutable directory fully safe without platform-specific openat2,
    # but detects ordinary symlink swaps and conflicting concurrent writes.
    try:
        _new_root, new_relative, new_target = _contained_target(root, relative_path)
        if new_relative != relative or new_target != target:
            raise ArtifactPathError("artifact output path changed during publication")
        if not _existing_matches(target, data, max_bytes=max_bytes):
            raise ArtifactConflictError("authority artifact publication was not stable")
    except FrontierArtifactIOError:
        if created:
            _rollback_owned_publication(target, parent, temp_identity)
        raise
    except (OSError, RuntimeError, ValueError):
        if created:
            _rollback_owned_publication(target, parent, temp_identity)
        raise FrontierArtifactIOError(
            "authority artifact could not be revalidated"
        ) from None
    return AuthorityArtifactWrite(
        relative_path=relative.as_posix(),
        content_sha256=digest,
        size_bytes=len(data),
        created=created,
    )


__all__ = [
    "ArtifactConflictError",
    "ArtifactFormatError",
    "LoadedContainedFile",
    "ArtifactPathError",
    "AuthorityArtifactWrite",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "DEFAULT_MAX_JSON_DEPTH",
    "DEFAULT_MAX_JSON_NODES",
    "FrontierArtifactIOError",
    "LoadedAuthorityArtifact",
    "MAX_SAFE_JSON_INTEGER",
    "MIN_SAFE_JSON_INTEGER",
    "canonical_authority_bytes",
    "canonical_authority_sha256",
    "load_authority_artifact",
    "load_canonical_authority_bytes",
    "load_contained_file",
    "resolve_contained_directory",
    "write_authority_artifact",
]
