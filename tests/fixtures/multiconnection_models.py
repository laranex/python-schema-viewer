"""Models on two connections for the multi-connection tests: Base for the application database, AnalyticsBase for
the analytics database. Both declare a users table. User.analytics_profile relates a default-connection model to an
analytics table without a foreign key (the tables live in different databases), through primaryjoin and foreign().
tests/test_multiconnection.py creates databases that match Base exactly and differ from AnalyticsBase on purpose:
analytics users.segment is wider and analytics users.legacy is extra."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, foreign, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AnalyticsBase(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"))


class AnalyticsUser(AnalyticsBase):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column()  # The application's users.id, in another database: no foreign key.
    segment: Mapped[str] = mapped_column(String(20))


class Event(AnalyticsBase):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))


# Another registry's class cannot be named by string, so the relationship is added once both are defined.
User.analytics_profile = relationship(AnalyticsUser, primaryjoin=User.id == foreign(AnalyticsUser.user_id),
                                      viewonly=True, uselist=False)


DEFAULT_SQL = (
    "CREATE TABLE teams (id INTEGER NOT NULL PRIMARY KEY);"
    "CREATE TABLE users (id INTEGER NOT NULL PRIMARY KEY, email VARCHAR(255) NOT NULL,"
    " team_id INTEGER REFERENCES teams (id));"
)
ANALYTICS_SQL = (
    "CREATE TABLE users (id INTEGER NOT NULL PRIMARY KEY, user_id INTEGER NOT NULL, segment VARCHAR(40) NOT NULL,"
    " legacy INTEGER);"
    "CREATE TABLE events (id INTEGER NOT NULL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users (id),"
    " name VARCHAR(100) NOT NULL);"
)
