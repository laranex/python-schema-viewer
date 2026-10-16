"""Synthetic Peewee models; the database is never initialised or connected."""

import datetime

from peewee import (
    SQL,
    Check,
    CharField,
    CompositeKey,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    ManyToManyField,
    Model,
    SqliteDatabase,
    fn,
)

database = SqliteDatabase(None)


class BaseModel(Model):
    class Meta:
        database = database


class Author(BaseModel):
    name = CharField(max_length=100, index=True)
    email = CharField(unique=True)
    joined = DateTimeField(default=datetime.datetime.now)
    score = IntegerField(default=0, constraints=[Check("score >= 0"), SQL("DEFAULT 0")])

    class Meta:
        table_name = "authors"
        indexes = ((("name", "email"), True),)
        constraints = [SQL("UNIQUE (email, name)"), SQL('CONSTRAINT "name_present" CHECK (length(name) > 0)')]


class Tag(BaseModel):
    label = CharField()


class Book(BaseModel):
    title = CharField()
    author = ForeignKeyField(Author, backref="books", on_delete="CASCADE", on_update="NO ACTION")
    editor = ForeignKeyField(Author, null=True, backref="+", deferrable="INITIALLY DEFERRED",
                             constraint_name="book_editor_fk")
    tags = ManyToManyField(Tag, backref="books")

    class Meta:
        schema = "library"


class Profile(BaseModel):
    author = ForeignKeyField(Author, unique=True, backref="profile")


class OrderLine(BaseModel):
    order = IntegerField()
    number = IntegerField()

    class Meta:
        primary_key = CompositeKey("order", "number")


class Unbound(Model):
    code = CharField(max_length=10)


Book.add_index(Book.title.desc(), name="book_title_desc")
Author.add_index(Author.index(fn.lower(Author.name), name="author_lower_name"))
Author.add_index(Author.email, where=(Author.score > 10), name="author_top_email")
Author.add_index(SQL("CREATE INDEX author_score_raw ON authors (score)"))
