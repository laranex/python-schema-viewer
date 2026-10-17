"""Runs the extraction worker at the requested isolation level and validates what it returns."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

from . import ExtractRequest, ExtractResult, IsolationConfig, IsolationError
from .env import dropped, worker_environment

MAX_RESULT_BYTES = 32 * 1024 * 1024
OUTPUT_TAIL = 4000


def extract(request: ExtractRequest, config: IsolationConfig) -> ExtractResult:
    if config.level == "process":
        return _process(request, config)
    if config.level == "container":
        return _container(request, config)
    raise IsolationError(f"Unknown isolation level {config.level!r}; choose process or container")


def _allowed_writes(project_root: Path, config: IsolationConfig) -> list[Path]:
    out = []
    for entry in config.allow_writes:
        path = (project_root / entry).resolve()
        if path != project_root and project_root not in path.parents:
            raise IsolationError(f"allow_writes entry {entry!r} is outside the project")
        out.append(path)
    return out


def _read_result(path: Path, output: str) -> ExtractResult:
    if not path.is_file():
        raise IsolationError(f"The extraction worker exited without a result.{_tail(output)}")
    if path.stat().st_size > MAX_RESULT_BYTES:
        raise IsolationError("The extraction worker's result exceeds 32 MiB")
    result = json.loads(path.read_bytes())
    if not result.get("ok"):
        raise IsolationError(f"Extraction failed in the isolated worker: {result.get('error')}{_tail(output)}")
    return ExtractResult(document=result["document"], blocked=list(result.get("blocked", [])))


def _log_tail(path: Path) -> str:
    with open(path, "rb") as log:
        log.seek(max(0, path.stat().st_size - OUTPUT_TAIL))
        return log.read().decode("utf-8", "replace")


def _tail(output: str) -> str:
    output = output.strip()
    return f"\nWorker output (last lines):\n{output[-OUTPUT_TAIL:]}" if output else ""


def _process(request: ExtractRequest, config: IsolationConfig) -> ExtractResult:
    project_root = Path(request.project_root).resolve()
    scratch = Path(tempfile.mkdtemp(prefix="lsv-extract-")).resolve()
    try:
        home, tmp, out = scratch / "home", scratch / "tmp", scratch / "out"
        for d in (home, tmp, out):
            d.mkdir()
        allowed = [tmp, out, home, *_allowed_writes(project_root, config)]
        env = worker_environment(os.environ, passthrough=config.env_passthrough, fixed=config.env,
                                 home=str(home), tmp=str(tmp))
        # The worker must import this package from where it is installed, wherever the project lives.
        package_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_root, env.get("PYTHONPATH")]))
        command = [sys.executable, "-B", "-m", "laranex_schema_viewer.isolation.worker", "--result",
                   str(out / "result.json"), *[a for d in allowed for a in ("--allow-write", str(d))]]
        posix = os.name == "posix"
        # Output goes to a file, not a pipe: a process the application leaves behind could hold a pipe open and
        # stall the build until the timeout.
        log = scratch / "output.log"
        with open(log, "wb") as output:
            process = subprocess.Popen(
                command, cwd=project_root, env=env, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=posix,
                creationflags=0 if posix else subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
            )
            try:
                process.stdin.write(json.dumps(asdict(request)).encode())  # type: ignore[union-attr]
                process.stdin.close()  # type: ignore[union-attr]
                process.wait(timeout=config.timeout)
            except subprocess.TimeoutExpired:
                raise IsolationError(f"Extraction did not finish within {config.timeout:g} seconds; the worker and "
                                     "everything it started were stopped") from None
            finally:
                # Whatever the application started (threads end with the process; child processes do not).
                _kill_group(process)
                process.wait()
        result = _read_result(out / "result.json", _log_tail(log))
        result.withheld = dropped(os.environ, env)
        return result
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _kill_group(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    else:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)


def _runtime(config: IsolationConfig) -> str:
    candidates = [config.container_runtime] if config.container_runtime else ["docker", "podman"]
    for name in candidates:
        path = shutil.which(name) if name else None
        if path:
            probe = subprocess.run([path, "info"], capture_output=True, timeout=30, check=False)
            if probe.returncode == 0:
                return path
    raise IsolationError(f"Container isolation needs a running {' or '.join(c for c in candidates if c)}; "
                         "none is available. The build stops instead of running with weaker isolation.")


def _container(request: ExtractRequest, config: IsolationConfig) -> ExtractResult:
    from .. import __version__

    if not config.container_image:
        raise IsolationError("Container isolation needs container_image: an image with the project's dependencies "
                             f"and laranex-schema-viewer {__version__} installed")
    runtime = _runtime(config)
    project_root = Path(request.project_root).resolve()
    base = [runtime, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", config.container_memory,
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m"]
    if os.name == "posix":
        base += ["--user", f"{os.getuid()}:{os.getgid()}"]
    probe = subprocess.run([*base, config.container_image, "python", "-c",
                            "import laranex_schema_viewer as m; print(m.__version__)"],
                           capture_output=True, text=True, timeout=120, check=False)
    if probe.returncode != 0 or probe.stdout.strip() != __version__:
        raise IsolationError(f"Image {config.container_image} must have laranex-schema-viewer {__version__} "
                             f"installed (found: {probe.stdout.strip() or probe.stderr.strip()[-300:] or 'nothing'})")
    out = Path(tempfile.mkdtemp(prefix="lsv-container-")).resolve()
    name = f"lsv-extract-{uuid.uuid4().hex[:12]}"
    try:
        allowed = ["/tmp", "/out", *[f"/project/{w}" for w in config.allow_writes]]
        env = worker_environment(os.environ, passthrough=config.env_passthrough, fixed=config.env, home="/tmp",
                                 tmp="/tmp")
        # The host's PATH, PYTHONPATH and virtual environment mean nothing inside the image.
        for key in ("PATH", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
            env.pop(key, None)
        command = [*base, "--name", name, "-i", "-v", f"{project_root}:/project:ro", "-v", f"{out}:/out:rw",
                   "-w", "/project", *[a for k, v in sorted(env.items()) for a in ("-e", f"{k}={v}")],
                   config.container_image, "python", "-B", "-m", "laranex_schema_viewer.isolation.worker",
                   "--result", "/out/result.json", *[a for d in allowed for a in ("--allow-write", d)]]
        payload = json.dumps({**asdict(request), "project_root": "/project"}).encode()
        try:
            result = subprocess.run(command, input=payload, capture_output=True, timeout=config.timeout, check=False)
        except subprocess.TimeoutExpired:
            subprocess.run([runtime, "kill", name], capture_output=True, check=False)
            raise IsolationError(f"Extraction did not finish within {config.timeout:g} seconds; the container was "
                                 "stopped") from None
        extracted = _read_result(out / "result.json", (result.stdout + result.stderr).decode("utf-8", "replace"))
        extracted.withheld = dropped(os.environ, env)
        return extracted
    finally:
        shutil.rmtree(out, ignore_errors=True)
