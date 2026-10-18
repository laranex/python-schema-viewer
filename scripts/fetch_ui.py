"""Install the shared laranex-schema-viewer-ui assets from the npm tarball that ui.lock.json pins.

    python scripts/fetch_ui.py                                     # download the locked tarball
    python scripts/fetch_ui.py --tarball ui.tgz                    # a local copy of the locked tarball
    python scripts/fetch_ui.py --tarball ui.tgz --allow-unverified # a local build that does not match the lock

The tarball's SHA-512 is checked against the lock's npm integrity before anything is read from it. Its members are
read into memory, never extracted: only regular files under package/ with plain relative names are accepted. Then
package.json must name the locked package and version, the viewer manifest must describe laranex-schema 2.x and match
every file it lists, and the shared contract files must be present. Only then are the installed locations replaced:

    src/laranex_schema_viewer/static/viewer/   the viewer build (dist/viewer), with the UI's LICENSE.txt and NOTICE.txt
    src/laranex_schema_viewer/contract/        schema.json and gate-report.schema.json
    tests/fixtures/contract/                   the schemas, fixtures, conformance cases and synthetic.json (contract/)

ui.stamp.json records the source (the URL, or the local file's name), its integrity, whether that matched the lock, and every installed file's SHA-256.
A local tarball that does not match the lock is installed only with --allow-unverified and stamped unverified; the
build hook (hatch_build.py) refuses to build an sdist or wheel from it. Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hatch_build import (CONTRACT_MAJOR, FIXTURES, LOCK, SCHEMAS, STAMP, VIEWER,  # noqa: E402
                         AssetError, check_assets)

MAX_TARBALL = 32 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
CONTRACT_FILES = ("schema.json", "gate-report.schema.json", "scan-report.schema.json", "allowlist.schema.json",
                  "synthetic.json")
CONTRACT_DIRECTORIES = ("fixtures/valid", "fixtures/invalid", "fixtures/gate/valid", "fixtures/gate/invalid",
                        "conformance/graph", "conformance/comparison", "conformance/requests",
                        "conformance/coverage-gaps", "conformance/overrides", "conformance/comparison-diagnostics",
                        "conformance/store", "conformance/migrations", "conformance/inference", "conformance/gate",
                        "conformance/scan")


class FetchError(Exception):
    pass


def integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise FetchError(f"The locked tarball URL must use https: {url}")
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(MAX_TARBALL + 1)
    if len(data) > MAX_TARBALL:
        raise FetchError(f"The tarball is larger than {MAX_TARBALL} bytes")
    return data


def read_members(data: bytes) -> dict[str, bytes]:
    """The tarball's files, keyed by their path below package/, after rejecting anything but plain regular files."""
    members: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            parts = member.name.split("/")
            if (not member.isreg() or "\\" in member.name or len(parts) < 2 or parts[0] != "package"
                    or any(p in ("", ".", "..") for p in parts)):
                raise FetchError(f"Unsafe tarball member: {member.name!r}")
            name = "/".join(parts[1:])
            if name in members:
                raise FetchError(f"Duplicate tarball member: {member.name!r}")
            total += member.size
            if total > MAX_UNPACKED:
                raise FetchError(f"The tarball unpacks to more than {MAX_UNPACKED} bytes")
            members[name] = archive.extractfile(member).read()
    return members


def plan(members: dict[str, bytes], lock: dict) -> dict[str, bytes]:
    """Installed path (relative to the repository) -> content, after validating the package's structure."""
    try:
        package = json.loads(members["package.json"])
        manifest = json.loads(members["dist/viewer/manifest.json"])
    except (KeyError, ValueError) as error:
        raise FetchError(f"Not a laranex-schema-viewer-ui package: {error}") from None
    if (package.get("name"), package.get("version")) != (lock["name"], lock["version"]):
        raise FetchError(f"The tarball is {package.get('name')} {package.get('version')}, ui.lock.json pins "
                         f"{lock['name']} {lock['version']}")
    if (manifest.get("name"), manifest.get("version"), manifest.get("contract", {}).get("major")) != (
            lock["name"], lock["version"], CONTRACT_MAJOR):
        raise FetchError(f"The viewer manifest does not describe {lock['name']} {lock['version']} for "
                         f"laranex-schema {CONTRACT_MAJOR}.x")
    files = {f"{VIEWER}/manifest.json": members["dist/viewer/manifest.json"]}
    for entry in manifest["files"]:
        name = f"dist/viewer/{entry['path']}"
        content = members.get(name)
        if ".." in PurePosixPath(entry["path"]).parts or content is None:
            raise FetchError(f"The viewer manifest lists a missing or unsafe file: {entry['path']!r}")
        if len(content) != entry["size"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise FetchError(f"{name} does not match the viewer manifest")
        files[f"{VIEWER}/{entry['path']}"] = content
    for name in ("LICENSE", "NOTICE"):
        if name not in members:
            raise FetchError(f"The package has no {name}")
        files[f"{VIEWER}/{name}.txt"] = members[name]
    for name in CONTRACT_FILES:
        if f"contract/{name}" not in members:
            raise FetchError(f"The package has no contract/{name}")
        files[f"{FIXTURES}/{name}"] = members[f"contract/{name}"]
    for target in SCHEMAS:
        files[target] = members[f"contract/{PurePosixPath(target).name}"]
    for directory in CONTRACT_DIRECTORIES:
        cases = [n for n in members if PurePosixPath(n).parent == PurePosixPath("contract", directory)
                 and n.endswith(".json")]
        if not cases:
            raise FetchError(f"The package has no contract/{directory} cases")
        files.update({f"{FIXTURES}/{n.removeprefix('contract/')}": members[n] for n in cases})
    return files


def install(root: Path, files: dict[str, bytes], stamp: dict) -> None:
    (root / STAMP).unlink(missing_ok=True)  # A partial install is never stamped.
    for directory in (VIEWER, FIXTURES):
        shutil.rmtree(root / directory, ignore_errors=True)
    for name, content in sorted(files.items()):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    stamp["files"] = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    (root / STAMP).write_text(json.dumps(stamp, indent=2) + "\n")


def fetch(root: Path, tarball: Path | None = None, allow_unverified: bool = False) -> dict:
    lock = json.loads((root / LOCK).read_text())
    data = tarball.read_bytes() if tarball else download(lock["tarball"])
    actual = integrity(data)
    verified = hmac.compare_digest(actual, lock["integrity"])
    if not verified and not (tarball and allow_unverified):
        raise FetchError(f"The tarball's integrity {actual} does not match ui.lock.json ({lock['integrity']})"
                         + ("; pass --allow-unverified to use a local build for development" if tarball else ""))
    files = plan(read_members(data), lock)
    stamp = {"name": lock["name"], "version": lock["version"], "integrity": actual,
             "source": f"file:{tarball.name}" if tarball else lock["tarball"], "verified": verified}
    install(root, files, stamp)
    return check_assets(root, allow_unverified=not verified)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tarball", type=Path, help="a local laranex-schema-viewer-ui tarball (npm pack)")
    parser.add_argument("--allow-unverified", action="store_true",
                        help="install a local tarball that does not match ui.lock.json, stamped unverified")
    args = parser.parse_args(argv)
    if args.allow_unverified and not args.tarball:
        parser.error("--allow-unverified needs --tarball")
    try:
        stamp = fetch(ROOT, args.tarball, args.allow_unverified)
    except (FetchError, AssetError, OSError, tarfile.TarError) as error:
        print(f"fetch_ui: {error}", file=sys.stderr)
        return 1
    state = "verified against ui.lock.json" if stamp["verified"] else (
        "UNVERIFIED: does not match ui.lock.json; sdists and wheels will not build")
    print(f"Installed {stamp['name']} {stamp['version']} ({len(stamp['files'])} files, {state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
