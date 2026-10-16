from __future__ import annotations

from sqlalchemy.orm import registry, relationship
from sqlmodel import Field, Relationship, SQLModel


class FixtureBase(SQLModel, registry=registry()):
    pass


class Squad(FixtureBase, table=True):
    __tablename__ = "fixture_squads"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    heroes: list["Hero"] = Relationship(
        sa_relationship=relationship("Hero", back_populates="squad")
    )


class Hero(FixtureBase, table=True):
    __tablename__ = "fixture_heroes"
    id: int | None = Field(default=None, primary_key=True)
    squad_id: int | None = Field(default=None, foreign_key="fixture_squads.id")
    squad: Squad | None = Relationship(
        sa_relationship=relationship("Squad", back_populates="heroes")
    )

