"""Runs DDL against a SQLite file through SQLAlchemy at import time."""
from sqlalchemy import create_engine

from ._common import attempt, models, target

Base, Team, Member = models("ddl")
OUTCOME = attempt(lambda: Base.metadata.create_all(create_engine(f"sqlite:///{target('SQLITE')}")))
