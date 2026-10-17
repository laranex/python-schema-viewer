"""Reads credentials from the environment and the home directory at import time."""
import json
import os
from pathlib import Path

from ._common import models

SEEN = {name: os.environ.get(name) for name in ("DATABASE_URL", "LSV_ADVERSARIAL_SECRET", "AWS_SECRET_ACCESS_KEY")}
FOUND = {"env": SEEN, "home": os.environ.get("HOME", ""), "pgpass": (Path.home() / ".pgpass").exists()}
Base, Team, Member = models("secret", comment=json.dumps(FOUND))
