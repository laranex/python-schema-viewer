from sqlalchemy import (
    Column, ForeignKey, ForeignKeyConstraint, Index, Integer, MetaData,
    String, Table, UniqueConstraint, func, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


membership = Table(
    "memberships", Base.metadata,
    Column("user_id", ForeignKey("users.id"), primary_key=True),
    Column("team_id", ForeignKey("teams.id"), primary_key=True),
)


def never_call():
    raise AssertionError("Default executed during inspection")


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    members: Mapped[list["User"]] = relationship(secondary=membership, back_populates="teams")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_active", "email", unique=True, postgresql_where=text("active = true"),
              postgresql_include=["name"]),
        Index("ix_users_email_lower", func.lower(text("email"))),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(default=never_call)
    active: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    manager: Mapped["User"] = relationship(remote_side=[id], back_populates="reports")
    reports: Mapped[list["User"]] = relationship(back_populates="manager")
    teams: Mapped[list[Team]] = relationship(secondary=membership, back_populates="members")


parent = Table("parent", Base.metadata, Column("a", Integer, primary_key=True),
               Column("b", Integer, primary_key=True), UniqueConstraint("b", "a", name="uq_ba"))
child = Table("child", Base.metadata, Column("a", Integer), Column("b", Integer),
              ForeignKeyConstraint(["a", "b"], ["parent.a", "parent.b"], name="fk_pair"))

# No database constraint: this remains a declared relationship, never a fake FK.
class Audit(Base):
    __tablename__ = "audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column()
    user: Mapped[User] = relationship(primaryjoin="foreign(Audit.user_id) == User.id", viewonly=True)


empty_metadata = MetaData()

