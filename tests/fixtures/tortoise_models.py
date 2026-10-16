"""Synthetic Tortoise models; initialised with Tortoise.init_models, never connected."""

from tortoise import fields
from tortoise.indexes import Index, PartialIndex
from tortoise.models import Model

try:
    from tortoise.migrations.constraints import CheckConstraint, UniqueConstraint
except ImportError:  # Tortoise < 1.0 has no declarative constraints.
    CheckConstraint = UniqueConstraint = None


def never_called():
    raise AssertionError("Callable defaults must not be invoked")


class Author(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=100, description="Display name", db_index=True)
    email = fields.CharField(max_length=200, unique=True)
    active = fields.BooleanField(default=True)
    token = fields.CharField(max_length=40, default=never_called)
    joined = fields.DatetimeField(null=True)

    class Meta:
        table = "authors"
        table_description = "People who write books"
        unique_together = (("name", "email"),)
        indexes = [Index(fields=("name", "active"), name="author_name_active"),
                   PartialIndex(fields=("email",), name="author_active_email", condition={"active": True})]
        if CheckConstraint is not None:
            constraints = [CheckConstraint(check="length(name) > 0", name="author_name_present"),
                           UniqueConstraint(fields=("token",), name="author_token")]


class Profile(Model):
    author = fields.OneToOneField("models.Author", related_name="profile", on_delete=fields.CASCADE)


class Tag(Model):
    label = fields.CharField(max_length=20)


class Book(Model):
    title = fields.CharField(max_length=200, default="untitled")
    author = fields.ForeignKeyField("models.Author", related_name="books", on_delete=fields.SET_NULL,
                                    null=True)
    editor = fields.ForeignKeyField("models.Author", related_name=False, db_constraint=False, null=True)
    tags = fields.ManyToManyField("models.Tag", related_name="books", through="book_tag")
    readers = fields.ManyToManyField("models.Author", related_name="read_books", through="reading",
                                     forward_key="reader_id", backward_key="book_id")

    class Meta:
        schema = "library"


class Reading(Model):
    book = fields.ForeignKeyField("models.Book", related_name="readings")
    reader = fields.ForeignKeyField("models.Author", related_name="readings")

    class Meta:
        table = "reading"
        schema = "library"
