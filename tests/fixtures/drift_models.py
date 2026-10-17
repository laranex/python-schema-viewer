"""Models for the reconciliation tests. tests/databases/drift-*.sql create databases that differ from them on purpose:
users.email is wider, users.status has another default, users.nickname is missing, users.legacy_flag is extra, the
users.team_id foreign key is not enforced, ix_users_email is unique, line_notes cascades differently, ghosts is
missing and audit_log is extra. Everything else matches."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index, Numeric, String, Table, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


memberships = Table(
    "memberships", Base.metadata,
    Column("user_id", ForeignKey("users.id"), primary_key=True),
    Column("team_id", ForeignKey("teams.id"), primary_key=True),
)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    users: Mapped[list[User]] = relationship(back_populates="team")
    members: Mapped[list[User]] = relationship(secondary=memberships, viewonly=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email"),
                      CheckConstraint("length(email) > 3", name="users_email_length"))
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    nickname: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'active'"))
    team: Mapped[Team | None] = relationship(back_populates="users")


class OrderLine(Base):
    __tablename__ = "order_lines"
    order_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    line_no: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class LineNote(Base):
    __tablename__ = "line_notes"
    __table_args__ = (ForeignKeyConstraint(["order_id", "line_no"], ["order_lines.order_id", "order_lines.line_no"],
                                           name="line_notes_line_fk", ondelete="CASCADE"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int]
    line_no: Mapped[int]
    line: Mapped[OrderLine] = relationship()


class Ghost(Base):
    __tablename__ = "ghosts"
    id: Mapped[int] = mapped_column(primary_key=True)
