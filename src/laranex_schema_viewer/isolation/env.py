"""The environment an isolated worker sees: an allowlist, never the parent's whole environment."""

from __future__ import annotations

import re
from collections.abc import Mapping

# Variables a Python program needs to start and find its packages. Everything else (DATABASE_URL, *_PASSWORD,
# cloud credentials, tokens) is left out unless the project passes it through by name.
ALLOWED = {"PATH", "LANG", "LANGUAGE", "TERM", "TZ", "PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONHASHSEED",
           "VIRTUAL_ENV", "CONDA_PREFIX", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "DJANGO_SETTINGS_MODULE"}
ALLOWED_PREFIXES = ("LC_",)
# Names that look like secrets are refused even when passed through, unless the project insists by listing them in
# env with a placeholder value.
SECRET = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|CREDENTIAL|PRIVATE|_KEY$|^DATABASE_URL$|_DSN$|_URI$|_URL$)", re.I)


def worker_environment(source: Mapping[str, str], *, passthrough: tuple[str, ...], fixed: tuple[tuple[str, str], ...],
                       home: str, tmp: str) -> dict[str, str]:
    """The allowlisted environment, with HOME and TMPDIR pointing at the worker's own scratch directory so that
    credential files under the real home directory are not found by default (they can still be read by path)."""
    refused = [name for name in passthrough if SECRET.search(name)]
    if refused:
        raise ValueError(f"env_passthrough must not carry credentials ({', '.join(sorted(refused))}); give the "
                         "extraction worker a placeholder in [tool.laranex-schema-viewer.env] instead")
    env = {k: v for k, v in source.items() if k in ALLOWED or k.startswith(ALLOWED_PREFIXES) or k in passthrough}
    env.update(dict(fixed))
    env.update(HOME=home, USERPROFILE=home, TMPDIR=tmp, TEMP=tmp, TMP=tmp, PYTHONDONTWRITEBYTECODE="1",
               PYTHONNOUSERSITE="1")
    return env


def dropped(source: Mapping[str, str], env: Mapping[str, str]) -> int:
    """How many variables were left out; their names and values are never printed."""
    return len([k for k in source if k not in env])
