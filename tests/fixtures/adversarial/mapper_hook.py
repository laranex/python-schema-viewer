"""Writes a file from a mapper configuration hook, which runs during extraction, not at import."""
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Mapper

from ._common import attempt, models, target

OUTCOME = "not run"
Base, Team, Member = models("hook")


@event.listens_for(Mapper, "after_configured")
def after_configured():
    global OUTCOME
    OUTCOME = attempt(lambda: Path(target("WRITE")).write_text("written by a mapper hook"))
