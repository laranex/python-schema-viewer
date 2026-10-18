"""The ``scan`` command: Git and file system input for the snapshot scanner, mirroring laranex-schema-viewer-ui's
contract/scan.mjs (its options, scopes, summary line and exit codes). The rules live in ``contract.scan``; this module
only lists and reads files and Git objects. Git runs with a fixed argument list and no shell. Nothing here prints file
content: the summary has counts only, and paths and reasons go only into the report.

Also the build's check that its output is not tracked or left unignored in a Git work tree (``output_status``).
"""

from __future__ import annotations

import argparse
import os
import sys
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from .contract import canonical_json
from .contract.scan import (SCAN_SIZE_LIMIT, ResolvedAllowlist, ScanFile, ScanInput, is_scan_candidate,
                            parse_allowlist_text, resolve_allowlist, scan_files)

DEFAULT_ALLOWLIST = ".laranex-schema-allowlist.json"
PROGRAM = "laranex-schema-viewer scan"
DESCRIPTION = ("Finds laranex-schema documents by content and judges them against the allowlist (default: "
               ".laranex-schema-allowlist.json at the Git top level, if present). Prints counts only; paths and "
               "reasons are in the laranex-schema-scan report. Exit codes: 0 clean; 1 a confirmed exposure; 2 the scan "
               "failed or is incomplete; 3 an uncertain exposure (2 > 1 > 3 > 0).")
# Blob reads are batched so that one batch holds at most this many bytes.
BATCH_BYTES = 256 * 1024 * 1024


class GitError(Exception):
    pass


@dataclass
class ScanOptions:
    tracked: bool = False
    untracked: bool = False
    history: bool = False
    artifacts: list[str] = field(default_factory=list)
    allowlist: str | None = None
    report: str | None = None
    json: bool = False


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _display(name: str) -> str:
    """A file name as UTF-8 text, as Node.js reads names (never a lone surrogate from undecodable bytes)."""
    return _text(os.fsencode(name))


def _posix(path: str) -> str:
    return path.replace(os.sep, "/")


def _run(argv: Sequence[str], data: bytes | None = None) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(list(argv), input=data, capture_output=True, check=False)
    except OSError:
        return None


def _git(root: str, args: Sequence[str], data: bytes | None = None) -> bytes:
    result = _run(["git", "-C", root, "-c", "core.quotePath=false", *args], data)
    if result is None or result.returncode != 0:
        raise GitError(f"git {args[0]} failed")
    return result.stdout


def top_level(cwd: str) -> str | None:
    """The Git work tree's top level (real path) for cwd, or None outside one or without Git."""
    result = _run(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
    if result is None or result.returncode != 0:
        return None
    return os.path.realpath(_text(result.stdout).strip())


def _read_blobs(root: str, oids: list[str], limit: int) -> dict[str, dict[str, Any]]:
    """{oid: {"data": bytes} | {"error": reason}}, never reading more than the limit per blob."""
    unique = list(dict.fromkeys(oids))
    result: dict[str, dict[str, Any]] = {}
    if not unique:
        return result
    sizes: dict[str, int] = {}
    for line in _text(_git(root, ["cat-file", "--batch-check"], ("\n".join(unique) + "\n").encode())).strip().split("\n"):
        oid, kind, size = (line.split(" ") + ["", ""])[:3]
        if kind != "blob":
            result[oid] = {"error": "read_error"}
        elif int(size) > limit:
            result[oid] = {"error": "size_limit"}
        else:
            sizes[oid] = int(size)
    batches: list[list[str]] = [[]]
    total = 0
    for oid, size in sizes.items():
        if total + size > BATCH_BYTES and batches[-1]:
            batches.append([])
            total = 0
        batches[-1].append(oid)
        total += size
    for batch in (b for b in batches if b):
        out = _git(root, ["cat-file", "--batch"], ("\n".join(batch) + "\n").encode())
        at = 0
        while at < len(out):
            end = out.index(b"\n", at)
            oid, _, size = _text(out[at:end]).split(" ")[:3]
            start = end + 1
            result[oid] = {"data": out[start:start + int(size)]}
            at = start + int(size) + 1
    return result


def _blob_files(root: str, entries: list[dict[str, Any]], scope: str, limit: int) -> list[ScanFile]:
    blobs = _read_blobs(root, [e["oid"] for e in entries], limit)
    return [ScanFile(scope, e["path"], e.get("commit"), blobs.get(e["oid"], {}).get("data"),
                     blobs.get(e["oid"], {}).get("error")) for e in entries]


def _tracked_files(root: str, limit: int) -> list[ScanFile]:
    """The index, read from the index: staged content, whatever the working tree holds. Symbolic links and submodules
    are not files."""
    entries = []
    for line in _text(_git(root, ["ls-files", "-s", "-z"])).split("\0"):
        if not line:
            continue
        head, _, path = line.partition("\t")
        mode, oid = head.split(" ")[:2]
        if mode in ("100644", "100755") and is_scan_candidate(path, "tree"):
            entries.append({"oid": oid, "path": path})
    return _blob_files(root, entries, "tracked", limit)


def _read_file(file: str, display: str, scope: str, limit: int) -> ScanFile:
    try:
        if os.stat(file).st_size > limit:
            return ScanFile(scope, display, error="size_limit")
        with open(file, "rb") as stream:
            return ScanFile(scope, display, data=stream.read())
    except OSError:
        return ScanFile(scope, display, error="read_error")


def _untracked_files(root: str, limit: int) -> list[ScanFile]:
    files = []
    for path in _text(_git(root, ["ls-files", "-z", "--others", "--exclude-standard"])).split("\0"):
        if not path or path.endswith("/") or not is_scan_candidate(path, "tree"):
            continue
        file = os.path.join(root, path)
        try:
            if not stat.S_ISREG(os.lstat(file).st_mode):
                continue
        except OSError:
            files.append(ScanFile("untracked", path, error="read_error"))
            continue
        files.append(_read_file(file, path, "untracked", limit))
    return files


def _artifact_files(directory: str, display: str, limit: int) -> list[ScanFile]:
    """Every file under the directory: symbolic links to files are read, symbolic links to directories are not
    followed (incomplete). A directory that cannot be listed, or a special file, is unreadable (incomplete)."""
    files: list[ScanFile] = []

    def walk(absolute: str, relative: str) -> None:
        shown_dir = f"{display}/{relative}" if display and relative else display or relative
        try:
            names = sorted(os.listdir(absolute))
        except OSError:
            files.append(ScanFile("artifacts", shown_dir or ".", error="read_error"))
            return
        for name in names:
            path = os.path.join(absolute, name)
            shown = f"{relative}/{_display(name)}" if relative else _display(name)
            label = f"{display}/{shown}" if display else shown
            try:
                mode = os.lstat(path).st_mode
                if stat.S_ISLNK(mode):
                    mode = os.stat(path).st_mode
                    if stat.S_ISDIR(mode):
                        files.append(ScanFile("artifacts", label, error="symlinked_directory"))
                        continue
            except OSError:
                files.append(ScanFile("artifacts", label, error="read_error"))
                continue
            if stat.S_ISDIR(mode):
                walk(path, shown)
            elif is_scan_candidate(label, "artifact"):
                files.append(_read_file(path, label, "artifacts", limit) if stat.S_ISREG(mode)
                             else ScanFile("artifacts", label, error="read_error"))

    walk(directory, "")
    return files


def _history_files(root: str, limit: int) -> tuple[list[ScanFile], str | None]:
    """Every version of a candidate file that a commit reachable from any ref introduced, merges included, once per
    path and blob, with the first commit git log lists for it. A shallow clone has no complete history."""
    if _text(_git(root, ["rev-parse", "--is-shallow-repository"])).strip() == "true":
        return [], "The repository is a shallow clone; its history is not available."
    if _text(_git(root, ["for-each-ref", "--count=1"])).strip() == "":
        return [], None
    tokens = _text(_git(root, ["log", "--all", "--no-renames", "--raw", "--no-abbrev", "-m", "--root", "-z",
                               "--format=%x01%H"])).split("\0")
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    commit = None
    i = 0
    while i < len(tokens):
        token = tokens[i].removeprefix("\n")
        if token.startswith("\x01"):
            commit = token[1:]
        elif token.startswith(":"):
            parts = token[1:].split(" ")
            mode, oid = parts[1], parts[3]
            i += 1
            path = tokens[i]
            if (mode in ("100644", "100755") and oid.strip("0") and is_scan_candidate(path, "tree")
                    and (path, oid) not in seen):
                seen[(path, oid)] = {"path": path, "oid": oid, "commit": commit}
        i += 1
    return _blob_files(root, list(seen.values()), "history", limit), None


def _ignored_paths(root: str, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    result = _run(["git", "-C", root, "check-ignore", "-z", "--stdin"], ("\0".join(paths) + "\0").encode())
    if result is None or result.returncode not in (0, 1):
        raise GitError("git check-ignore failed")
    return {p for p in _text(result.stdout).split("\0") if p}


def _relative_display(root: str, absolute: str) -> str:
    """An artifact directory as reports name it: relative to the root, '' for the root, absolute outside it."""
    try:
        relative = _posix(os.path.relpath(absolute, root))
    except ValueError:  # Another drive.
        return _posix(absolute)
    if relative == ".":
        return ""
    return _posix(absolute) if relative == ".." or relative.startswith("../") or os.path.isabs(relative) else relative


def scan(options: ScanOptions, cwd: str, limit: int = SCAN_SIZE_LIMIT) -> tuple[dict[str, Any], list[str]]:
    """The report and the scopes in the order scanned (for the summary)."""
    uses_git = options.tracked or options.untracked or options.history
    cwd = os.path.realpath(cwd)
    top = top_level(cwd)
    root = top or cwd
    errors: list[dict[str, str]] = []
    files: list[ScanFile] = []
    scopes: list[str] = []
    artifact_dirs: list[str] = []
    if uses_git and not top:
        errors.append({"kind": "not_a_repository", "message": "The Git scopes need a Git work tree; this is not one."})
    else:
        try:
            if options.tracked:
                scopes.append("tracked")
                files += _tracked_files(root, limit)
            if options.untracked:
                scopes.append("untracked")
                files += _untracked_files(root, limit)
            if options.history:
                scopes.append("history")
                history, problem = _history_files(root, limit)
                if problem:
                    errors.append({"kind": "history_unavailable", "message": problem})
                files += history
        except GitError as error:
            errors.append({"kind": "git_failed", "message": str(error)})
    for directory in options.artifacts:
        scopes.append("artifacts")
        absolute = os.path.realpath(os.path.join(cwd, directory))
        if not os.path.isdir(absolute):
            errors.append({"kind": "artifact_missing", "message": f"The artifact directory {directory} does not exist."})
            continue
        display = _relative_display(root, absolute)
        artifact_dirs.append(display)
        files += _artifact_files(absolute, display, limit)

    allowlist: ResolvedAllowlist | None = None
    allowlist_file = os.path.abspath(os.path.join(cwd, options.allowlist) if options.allowlist
                                     else os.path.join(root, DEFAULT_ALLOWLIST))
    if options.allowlist or os.path.exists(allowlist_file):
        base = os.path.dirname(allowlist_file)

        def load(path: str) -> Any:
            with open(os.path.join(base, path), "rb") as stream:
                return parse_allowlist_text(_text(stream.read()))

        allowlist = resolve_allowlist(os.path.basename(allowlist_file), load)

    # A stale entry names a path the scan covered: under a scanned artifact directory, or anywhere Git does not ignore
    # when both the tracked and the untracked files were scanned.
    def under_artifacts(path: str) -> bool:
        return any(d == "" or path == d or path.startswith(f"{d}/") for d in artifact_dirs)

    both = options.tracked and options.untracked and top is not None
    ignored: set[str] = set()
    if both and allowlist:
        try:
            ignored = _ignored_paths(root, [e["path"] for e in allowlist.entries if not under_artifacts(e["path"])])
        except GitError as error:
            errors.append({"kind": "git_failed", "message": str(error)})

    def covered(path: str) -> bool:
        return under_artifacts(path) or (both and path not in ignored)

    report = scan_files(ScanInput(scopes=scopes, files=files, allowlist=allowlist, errors=errors, limit=limit,
                                  covered=covered))
    return report, scopes


def summary(report: dict[str, Any], scopes: list[str]) -> str:
    """The one-line summary, worded as scan.mjs words it: counts only."""
    c = report["counts"]
    return (f"laranex-schema-scan: {c['files']} files ({', '.join(scopes)}); {c['confirmed']} confirmed, "
            f"{c['tampered']} tampered, {c['moved']} moved, {c['uncertain']} uncertain, {c['incomplete']} incomplete, "
            f"{c['allowed']} allowed, {c['stale']} stale; exit {report['exitCode']}")


def run(options: ScanOptions, *, cwd: str | None = None, stdout: IO[str], stderr: IO[str],
        limit: int = SCAN_SIZE_LIMIT, write: Callable[[bytes, str], None] | None = None) -> int:
    """Scans, prints the summary or the report, writes the report on request, and returns the exit code: 0 clean,
    1 confirmed exposure, 2 scan failure or incomplete scan, 3 uncertain exposure."""
    from .snapshot import write_atomically

    cwd = cwd or os.getcwd()
    report, scopes = scan(options, cwd, limit)
    text = canonical_json(report)
    if options.report:
        try:
            (write or write_atomically)(text.encode("utf-8"), os.path.join(cwd, options.report))
        except (OSError, ValueError) as error:
            print(f"{PROGRAM}: the report could not be written: {error.__class__.__name__}", file=stderr)
            return 2
    if options.json:
        stdout.write(text)
    else:
        stdout.write(summary(report, scopes) + "\n")
        if report["exitCode"] != 0 and not options.report:
            stdout.write("Paths and reasons are in the report: --report FILE or --json.\n")
    for error in report["errors"]:
        stderr.write(f"{PROGRAM}: {error['kind']}: {error['message']}\n")
    return report["exitCode"]


def output_status(path: str | os.PathLike[str]) -> str | None:
    """For a build's output path: "tracked" when the Git index has it, "unignored" when it is in a Git work tree and
    not ignored, else None (ignored, outside a work tree, or Git unavailable)."""
    target = Path(os.path.abspath(path))
    existing = target.parent
    while not existing.is_dir() and existing != existing.parent:
        existing = existing.parent
    top = top_level(str(existing))
    if top is None:
        return None
    resolved = Path(os.path.realpath(target.parent)) / target.name
    try:
        relative = resolved.relative_to(top).as_posix()
    except ValueError:
        return None
    if relative.split("/")[0] == ".git":
        return None
    tracked = _run(["git", "--literal-pathspecs", "-C", top, "ls-files", "-z", "--", relative])
    if tracked is None or tracked.returncode != 0:
        return None
    if tracked.stdout:
        return "tracked"
    ignored = _run(["git", "-C", top, "check-ignore", "-q", "--", relative])
    return "unignored" if ignored is not None and ignored.returncode == 1 else None


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tracked", action="store_true",
                        help="Every file in the Git index, read from the index (staged bytes, and files tracked before "
                             "they were ignored)")
    parser.add_argument("--untracked", action="store_true", help="Files Git would add: untracked and not ignored")
    parser.add_argument("--artifacts", action="append", default=[], metavar="DIR",
                        help="A build output directory, every file, ignore rules not applied; repeatable")
    parser.add_argument("--history", action="store_true",
                        help="Every version of a .json, .html or .htm file reachable from any ref (fails on a shallow "
                             "clone)")
    parser.add_argument("--allowlist", metavar="FILE", help="A laranex-schema-allowlist file")
    parser.add_argument("--report", metavar="FILE", help="Write the laranex-schema-scan report (JSON) to FILE")
    parser.add_argument("--json", action="store_true", help="Print the report instead of the summary")


def command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """The scan command's exit code; a usage error exits 2 without a report, as scan.mjs does."""
    if not (args.tracked or args.untracked or args.history or args.artifacts):
        parser.print_usage(sys.stderr)
        print(f"{parser.prog}: error: name at least one scope", file=sys.stderr)
        return 2
    options = ScanOptions(tracked=args.tracked, untracked=args.untracked, history=args.history,
                          artifacts=args.artifacts, allowlist=args.allowlist, report=args.report, json=args.json)
    try:
        return run(options, stdout=sys.stdout, stderr=sys.stderr)
    except (Exception, KeyboardInterrupt) as error:
        print(f"{parser.prog}: error: {type(error).__name__}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    """The command alone, without the rest of the CLI (the pre-commit hook runs it from the package source)."""
    parser = argparse.ArgumentParser(prog=PROGRAM, description=DESCRIPTION, allow_abbrev=False)
    add_arguments(parser)
    return command(parser.parse_args(argv), parser)
