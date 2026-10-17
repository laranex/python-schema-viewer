from __future__ import annotations

import os

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def target(name: str) -> str:
    return os.environ.get(f"LSV_ADVERSARIAL_{name}", "")


def attempt(action) -> str:
    try:
        action()
        return "succeeded"
    except PermissionError as error:
        return f"blocked: {error}"
    except Exception as error:  # noqa: BLE001
        return f"failed: {type(error).__name__}: {error}"


def models(prefix: str, comment: str | None = None):
    """Two related tables, so extraction has relationships to keep. The comment carries a probe's findings back
    through the extracted document, the only channel out of the worker."""

    class Base(DeclarativeBase):
        pass

    class Team(Base):
        __tablename__ = f"{prefix}_teams"
        __table_args__ = {"comment": comment}
        id: Mapped[int] = mapped_column(primary_key=True)

    class Member(Base):
        __tablename__ = f"{prefix}_members"
        id: Mapped[int] = mapped_column(primary_key=True)
        team_id: Mapped[int] = mapped_column(ForeignKey(f"{prefix}_teams.id"))
        team = relationship(Team)

    return Base, Team, Member
