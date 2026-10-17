"""Writes a file into the project at import time."""
from pathlib import Path

from ._common import attempt, models, target

OUTCOME = attempt(lambda: Path(target("WRITE")).write_text("written at import"))
Base, Team, Member = models("write")
