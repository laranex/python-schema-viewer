"""Snapshot scanning, a port of laranex-schema-viewer-ui's scan.ts.

Finds laranex-schema documents (and the legacy formats still in use) in the files a repository tracks, the files it
would add, build artifacts and, optionally, its history, so a private schema is not committed or published by
accident. docs/contract.md, "Snapshot scanning", is the normative description; contract/conformance/scan/ checks it.
Everything here is pure: ``laranex_schema_viewer.scan`` reads Git and the file system and hands the bytes to
``scan_files()``. The shapes both check and describe themselves as JSON Schema, so ``scan_report_json_schema()`` and
``allowlist_json_schema()`` reproduce the shared ``scan-report.schema.json`` and ``allowlist.schema.json``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .ids import by_list
from .serialize import FORMAT
from .shape import Context, Shape, array, integer, literal, nullable, obj, one_of, string, stringify

SCAN_FORMAT = "laranex-schema-scan"
SCAN_VERSION = "1.0"
ALLOWLIST_FORMAT = "laranex-schema-allowlist"
ALLOWLIST_VERSION = "1.0"
# Files larger than this are not read; the scan is then incomplete (exit 2).
SCAN_SIZE_LIMIT = 64 * 1024 * 1024

SCAN_SCOPES = ("artifacts", "history", "tracked", "untracked")
SCAN_KINDS = ("allowlist_entry", "document", "embedded", "legacy", "too_large", "uncertain", "unreadable")
SCAN_STATUSES = ("allowed", "confirmed", "incomplete", "moved", "stale", "tampered", "uncertain")
SCAN_REASONS = ("document", "embedded_json", "laravel_envelope", "missing_connections", "nested_document",
                "nested_legacy", "not_detected", "not_found", "object_literal", "read_error", "size_limit",
                "symlinked_directory", "unparseable_json", "viewer_global", "viewer_v1")
SCAN_ERROR_KINDS = ("allowlist_invalid", "artifact_missing", "git_failed", "history_unavailable", "not_a_repository")
SCAN_SCHEMA_ID = "https://laranex.github.io/schema-viewer-ui/laranex-schema-scan/1.0.json"
ALLOWLIST_SCHEMA_ID = "https://laranex.github.io/schema-viewer-ui/laranex-schema-allowlist/1.0.json"

# tree: what a repository holds (tracked, untracked, history): JSON and HTML. artifact: build output, where JavaScript
# and source maps are checked too.
EXTENSIONS = {"tree": (".json", ".html", ".htm"), "artifact": (".json", ".html", ".htm", ".js", ".mjs", ".cjs", ".map")}


def scan_mode(scope: str) -> str:
    return "artifact" if scope == "artifacts" else "tree"


def _extension(path: str) -> str:
    name = path[path.rfind("/") + 1:].lower()
    dot = name.rfind(".")
    return "" if dot <= 0 else name[dot:]


def is_scan_candidate(path: str, mode: str) -> bool:
    """Whether a file of this name is read at all in this mode. Other files are not content-checked."""
    return _extension(path) in EXTENSIONS[mode]


# The patterns are the reference's JavaScript ones, translated: JavaScript's \s, \d, . and $ differ from Python's.
_WS = "[\t\n\v\f\r    -     　﻿]*"
_ESC = r"\\{0,3}"
_Q = _ESC + "[\"']"
_FORMAT = re.escape(FORMAT)
_VERSION_TEXT = re.compile(r"[0-9]+\.[0-9]+\Z")
_LEGACY_V1 = f"{FORMAT}-viewer"
# The deepest a nested document is looked for: the number of keys and indexes from the top-level value to it.
_MAX_DEPTH = 6
# Quotes may be escaped up to three times (JSON in a JavaScript string in a source map); the value must be exactly the
# format, so laranex-schema-gate and a JSON Schema's "format": { "const": … } never match.
_QUOTED_FORMAT = re.compile(f'{_ESC}"format{_ESC}"{_WS}:{_WS}{_ESC}"{_FORMAT}{_ESC}"')
_LITERAL_FORMAT = re.compile(f"[{{,]{_WS}format{_WS}:{_WS}{_Q}{_FORMAT}{_Q}{_WS}[,}}]")
_CONNECTIONS = re.compile(f"{_ESC}[\"']?connections{_ESC}[\"']?{_WS}:{_WS}\\[")
# What else an unparseable document would show when its format key was cut off.
_DOCUMENT_KEYS = re.compile(f'"(derivedLinks|comparisons|inferredAssociations|relationships|models)"{_WS}:')
# The viewer manifest's contract: { format, major } names the format without being a document.
_IN_CONTRACT = re.compile(f"contract{_ESC}[\"']?{_WS}:{_WS}\\{{[^{{}}]*\\Z")
_VIEWER_GLOBAL = re.compile(f"window\\.__SCHEMA_VIEWER__{_WS}={_WS}\\{{")
# How far back a pair is checked for a manifest's contract key, in UTF-16 code units as the reference counts them.
_CONTRACT_WINDOW = 200


def _is_document(v: Any) -> bool:
    return (isinstance(v, dict) and v.get("format") == FORMAT and isinstance(v.get("version"), str)
            and _VERSION_TEXT.match(v["version"]) is not None and isinstance(v.get("connections"), list))


def _is_number(v: Any, n: int) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == n


# Temporary (decision 6), until Laravel and the Python and Node.js v1 snapshots are gone: Laravel's envelope, format 2
# with document.connections, and the v1 viewer format with its tables.
def _legacy_reason(v: Any) -> str | None:
    if not isinstance(v, dict):
        return None
    if _is_number(v.get("format"), 2) and isinstance(v.get("document"), dict) and isinstance(
            v["document"].get("connections"), list):
        return "laravel_envelope"
    if v.get("format") == _LEGACY_V1 and isinstance(v.get("tables"), list):
        return "viewer_v1"
    return None


def _nested(value: Any) -> str | None:
    """A nested document or legacy snapshot at depth 1.._MAX_DEPTH; a document wins over a legacy one."""
    legacy = False
    stack = [(value, 0)]
    while stack:
        v, depth = stack.pop()
        if depth > 0 and _is_document(v):
            return "nested_document"
        if depth > 0 and _legacy_reason(v):
            legacy = True
            continue
        if depth < _MAX_DEPTH and isinstance(v, (dict, list)):
            stack.extend((child, depth + 1) for child in (v.values() if isinstance(v, dict) else v))
    return "nested_legacy" if legacy else None


def _window_start(text: str, at: int) -> int:
    """Where a window of _CONTRACT_WINDOW UTF-16 code units ending at ``at`` starts."""
    start, units = at, 0
    while start > 0 and units < _CONTRACT_WINDOW:
        start -= 1
        units += 2 if ord(text[start]) > 0xFFFF else 1
    return start


def _format_hit(text: str) -> str | None:
    """The first format pair in the text that is not a manifest's contract, as "quoted" or "literal"."""
    for pattern, kind in ((_QUOTED_FORMAT, "quoted"), (_LITERAL_FORMAT, "literal")):
        for m in pattern.finditer(text):
            at = m.start() + m.group().index("format")
            if not _IN_CONTRACT.search(text, _window_start(text, at), at):
                return kind
    return None


def _decode(data: bytes) -> str:
    # TextDecoder drops one byte order mark, and the reference drops another.
    text = data.decode("utf-8", "replace")
    for _ in range(2):
        if text.startswith("﻿"):
            text = text[1:]
    return text


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _parse_json(text: str) -> Any:
    """JSON.parse: no NaN or Infinity, and every number a float, as JavaScript reads it."""
    return json.loads(text, parse_constant=_reject_constant, parse_int=float)


def _scan_json(text: str) -> dict[str, str] | None:
    try:
        value = _parse_json(text)
    except (ValueError, RecursionError):
        # Python's parser gives up on nesting deeper than its recursion limit, which JSON.parse reads: such a file
        # holding the format pair is uncertain here, never clean.
        if _format_hit(text) or (_CONNECTIONS.search(text) and _DOCUMENT_KEYS.search(text)):
            return {"kind": "uncertain", "reason": "unparseable_json"}
        return None
    if _is_document(value):
        return {"kind": "document", "reason": "document"}
    legacy = _legacy_reason(value)
    if legacy:
        return {"kind": "legacy", "reason": legacy}
    inner = _nested(value)
    return {"kind": "embedded", "reason": inner} if inner else None


def _scan_text(text: str) -> dict[str, str] | None:
    hit = _format_hit(text)
    if not hit:
        return None
    if not _CONNECTIONS.search(text):
        return {"kind": "uncertain", "reason": "missing_connections"}
    reason = "viewer_global" if _VIEWER_GLOBAL.search(text) else "embedded_json" if hit == "quoted" else "object_literal"
    return {"kind": "embedded", "reason": reason}


def scan_content(path: str, data: bytes, *, mode: str = "tree", limit: int = SCAN_SIZE_LIMIT) -> dict[str, str] | None:
    """Classifies one file by its content as {kind, reason}. None when the file is clean or not read in this mode (see
    is_scan_candidate). Never raises on any content and never returns any of it."""
    if not is_scan_candidate(path, mode):
        return None
    if len(data) > limit:
        return {"kind": "too_large", "reason": "size_limit"}
    text = _decode(data)
    if _extension(path) == ".json":
        return _scan_json(text)
    return _scan_text(text) if FORMAT in text else None


def allowlist_digest(data: bytes) -> str:
    """The allowlist digest: SHA-256 of the bytes with every CRLF read as LF, so a Windows checkout matches."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


# Allowlists.

# A relative POSIX path: no leading slash, no backslash, no empty, "." or ".." segment, no trailing slash.
RELATIVE_PATH = "^(?!/)(?!.*//)(?!.*/$)(?!(.*/)?\\.\\.?(/|$))[^\\\\\\u0000]+$"
_DOT = "[^\n\r  ]"
_PY_RELATIVE_PATH = f"^(?!/)(?!{_DOT}*//)(?!{_DOT}*/\\Z)(?!({_DOT}*/)?\\.\\.?(/|\\Z))[^\\\\\\x00]+\\Z"
SHA256 = "^[0-9a-f]{64}$"


def _relative_path() -> Shape:
    return string(pattern=RELATIVE_PATH, python_pattern=_PY_RELATIVE_PATH)


allowlist_shape = obj({
    "format": literal(ALLOWLIST_FORMAT),
    "version": literal(ALLOWLIST_VERSION),
    "entries": array(obj({"path": _relative_path(), "sha256": string(pattern=SHA256)})),
    "include?": array(obj({"path": _relative_path(),
                           "root": string(pattern=f"^\\.$|{RELATIVE_PATH}",
                                          python_pattern=f"^\\.\\Z|{_PY_RELATIVE_PATH}")})),
})


def allowlist_json_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of laranex-schema-allowlist 1.0, as the shared allowlist.schema.json."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": ALLOWLIST_SCHEMA_ID,
        "title": "laranex-schema-allowlist 1.0",
        **allowlist_shape.schema,
    }


def validate_allowlist(value: Any) -> list[str]:
    """The errors of an allowlist, always read strictly: an unknown property or another version is an error."""
    errors: list[str] = []
    allowlist_shape.check(value, "$", Context(errors=errors, strict=True))
    if not errors:
        seen: set[tuple[str, str]] = set()
        for i, e in enumerate(value["entries"]):
            key = (e["path"], e["sha256"])
            if key in seen:
                errors.append(f"$.entries[{i}]: {e['path']} with this sha256 is listed more than once")
            seen.add(key)
    return errors


def _dirname(path: str) -> str:
    return path[:path.rfind("/")] if "/" in path else ""


def _join(directory: str, path: str) -> str:
    return path if directory in ("", ".") else directory if path == "." else f"{directory}/{path}"


@dataclass(frozen=True)
class ResolvedAllowlist:
    entries: list[dict[str, str]]
    errors: list[str]


def resolve_allowlist(path: str, load: Callable[[str], Any]) -> ResolvedAllowlist:
    """Reads an allowlist and its includes. ``load(path)`` returns the parsed JSON of the file at path (POSIX, include
    paths joined to the including file's directory) and raises when it cannot. Entries of an included file have its
    root prepended, roots of nested includes compose, and a cycle is an error."""
    entries: dict[tuple[str, str], dict[str, str]] = {}
    errors: list[str] = []

    def visit(file: str, root: str, chain: list[str]) -> None:
        if file in chain:
            errors.append(f"Allowlist {file} includes itself")
            return
        try:
            value = load(file)
        except Exception:
            errors.append(f"Allowlist {file} could not be read as JSON")
            return
        problems = validate_allowlist(value)
        if problems:
            errors.extend(f"Allowlist {file}: {e}" for e in problems)
            return
        for e in value["entries"]:
            entry = {"path": _join(root, e["path"]), "sha256": e["sha256"]}
            entries[(entry["path"], entry["sha256"])] = entry
        for include in value.get("include", []):
            visit(_join(_dirname(file), include["path"]), _join(root, include["root"]), [*chain, file])

    visit(path, ".", [])
    return ResolvedAllowlist([entries[k] for k in sorted(entries)], errors)


def parse_allowlist_text(text: str) -> Any:
    """An allowlist file's JSON, read as the reference's JSON.parse reads it."""
    return _parse_json(text)


# Reports.


def _exit_code() -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in (0, 1, 2, 3):
            context.errors.append(f"{path}: expected 0, 1, 2 or 3, got {stringify(value)}")

    return Shape({"enum": [0, 1, 2, 3]}, check)


def _count() -> Shape:
    return integer(minimum=0)


COUNT_KEYS = ("files", "confirmed", "tampered", "moved", "uncertain", "allowed", "incomplete", "stale")
scan_report_shape = obj({
    "format": literal(SCAN_FORMAT),
    "version": string(pattern="^1\\.(0|[1-9][0-9]*)$"),
    "exitCode": _exit_code(),
    "scopes": array(one_of(SCAN_SCOPES), unique_items=True),
    "limit": integer(minimum=1),
    "counts": obj({key: _count() for key in COUNT_KEYS}),
    "findings": array(obj({
        "scope": one_of((*SCAN_SCOPES, "allowlist")),
        "status": one_of(SCAN_STATUSES),
        "kind": one_of(SCAN_KINDS),
        "path": string(min_length=1),
        "commit": nullable(string(pattern="^[0-9a-f]{40}([0-9a-f]{24})?$")),
        "sha256": nullable(string(pattern=SHA256)),
        "reason": one_of(SCAN_REASONS),
    })),
    "errors": array(obj({"kind": one_of(SCAN_ERROR_KINDS), "message": string(min_length=1)})),
})


def scan_report_json_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of laranex-schema-scan 1.x, as the shared scan-report.schema.json."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCAN_SCHEMA_ID,
        "title": "laranex-schema-scan 1.x report",
        **scan_report_shape.schema,
    }


def _finding_order(f: Mapping[str, Any]) -> list[str]:
    return [f["path"], f["commit"] or "", f["scope"], f["status"], f["reason"]]


_EXPOSED = ("confirmed", "tampered", "moved")


def scan_exit_code(findings: Iterable[Mapping[str, Any]], errors: list[Any]) -> int:
    """0 clean, 1 confirmed exposure, 2 scan failure or incomplete scan, 3 uncertain exposure; 2 > 1 > 3 > 0."""
    statuses = {f["status"] for f in findings}
    if errors or "incomplete" in statuses:
        return 2
    if statuses.intersection(_EXPOSED):
        return 1
    return 3 if "uncertain" in statuses else 0


@dataclass(frozen=True)
class ScanFile:
    scope: str
    path: str
    commit: str | None = None
    # The file's bytes, or why it could not be read: read_error, size_limit or symlinked_directory.
    data: bytes | None = None
    error: str | None = None


@dataclass
class ScanInput:
    scopes: list[str]
    files: list[ScanFile]
    # The resolved allowlist, or None without one.
    allowlist: ResolvedAllowlist | None = None
    errors: list[dict[str, str]] = field(default_factory=list)
    limit: int = SCAN_SIZE_LIMIT
    # Whether the scan covered this path, so an entry naming it that matched nothing is stale even if the file is
    # gone. Paths the scan read are always covered. Defaults to every path.
    covered: Callable[[str], bool] | None = None


def scan_files(scan: ScanInput) -> dict[str, Any]:
    """The scan: classifies every file, judges what it found against the allowlist, and returns the report."""
    limit = scan.limit
    entries = scan.allowlist.entries if scan.allowlist else []
    errors = [*scan.errors, *({"kind": "allowlist_invalid", "message": m}
                              for m in (scan.allowlist.errors if scan.allowlist else []))]
    by_path: dict[str, list[dict[str, str]]] = {}
    for e in entries:
        by_path.setdefault(e["path"], []).append(e)
    by_hash = {e["sha256"] for e in entries}
    matched: set[tuple[str, str]] = set()
    examined: dict[str, str] = {}
    findings: list[dict[str, Any]] = []
    files = 0
    for file in scan.files:
        # A file that could not be read is always reported; others are read only when their name makes them candidates.
        if not file.error and not is_scan_candidate(file.path, scan_mode(file.scope)):
            continue
        files += 1
        commit = file.commit

        def remember(state: str, file: ScanFile = file) -> None:
            if file.scope != "history" and examined.get(file.path) != "judged":
                examined[file.path] = state

        def finding(status: str, kind: str, digest: str | None, reason: str, file: ScanFile = file) -> dict[str, Any]:
            return {"scope": file.scope, "status": status, "kind": kind, "path": file.path, "commit": commit,
                    "sha256": digest, "reason": reason}

        if file.error or file.data is None:
            reason = file.error or "read_error"
            findings.append(finding("incomplete", "too_large" if reason == "size_limit" else "unreadable", None, reason))
            remember("judged")
            continue
        match = scan_content(file.path, file.data, mode=scan_mode(file.scope), limit=limit)
        if not match:
            remember("clean")
            continue
        remember("judged")
        if match["kind"] == "too_large":
            findings.append(finding("incomplete", "too_large", None, "size_limit"))
            continue
        digest = allowlist_digest(file.data)
        listed = by_path.get(file.path, [])
        allowed = next((e for e in listed if e["sha256"] == digest), None)
        if allowed:
            matched.add((allowed["path"], allowed["sha256"]))
        status = ("allowed" if allowed else "tampered" if listed else "moved" if digest in by_hash
                  else "uncertain" if match["kind"] == "uncertain" else "confirmed")
        findings.append(finding(status, match["kind"], digest, match["reason"]))
    covered = scan.covered or (lambda path: True)
    for entry in entries:
        if any((e["path"], e["sha256"]) in matched for e in by_path[entry["path"]]):
            continue
        state = examined.get(entry["path"])
        if state == "clean" or (state is None and covered(entry["path"])):
            findings.append({"scope": "allowlist", "status": "stale", "kind": "allowlist_entry", "path": entry["path"],
                             "commit": None, "sha256": entry["sha256"],
                             "reason": "not_detected" if state == "clean" else "not_found"})
    findings.sort(key=_finding_order)
    unique_errors = sorted({(e["kind"], e["message"]) for e in errors})
    sorted_errors = [{"kind": kind, "message": message} for kind, message in unique_errors]
    counts = {"files": files, **{s: sum(1 for f in findings if f["status"] == s)
                                 for s in ("confirmed", "tampered", "moved", "uncertain", "allowed", "incomplete",
                                           "stale")}}
    return {
        "format": SCAN_FORMAT,
        "version": SCAN_VERSION,
        "exitCode": scan_exit_code(findings, sorted_errors),
        "scopes": sorted(set(scan.scopes)),
        "limit": limit,
        "counts": counts,
        "findings": findings,
        "errors": sorted_errors,
    }


def validate_scan_report(value: Any, *, strict: bool = False) -> list[str]:
    """The errors of a scan report: the shape, then the rules (validateScanReport)."""
    errors: list[str] = []
    scan_report_shape.check(value, "$", Context(errors=errors, strict=strict))
    if not errors:
        r = value
        if strict and r["version"] != SCAN_VERSION:
            errors.append(f"$.version: strict validation is for version {SCAN_VERSION}, got {stringify(r['version'])}")
        expected = scan_exit_code(r["findings"], r["errors"])
        if r["exitCode"] != expected:
            errors.append(f"Exit code {int(r['exitCode'])} does not follow the errors and findings (expected {expected})")
        if any(r["counts"][s] != sum(1 for f in r["findings"] if f["status"] == s)
               for s in ("confirmed", "tampered", "moved", "uncertain", "allowed", "incomplete", "stale")):
            errors.append("Counts do not count the findings")
        for i, f in enumerate(r["findings"]):
            if i > 0 and by_list(_finding_order(r["findings"][i - 1]), _finding_order(f)) > 0:
                errors.append(f"Findings are not sorted by path, commit, scope, status and reason: findings[{i}]")
            if (f["scope"] == "history") != (f["commit"] is not None):
                errors.append(f"A finding has a commit exactly when it is from the history: findings[{i}]")
            if (f["scope"] == "allowlist") != (f["status"] == "stale"):
                errors.append(f"Only a stale allowlist entry has the scope allowlist: findings[{i}]")
    return errors
