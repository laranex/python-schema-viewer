import django
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

CHECK = "condition" if django.VERSION >= (5, 1) else "check"


def never_called():
    raise AssertionError("Callable defaults must not be invoked")


class Author(models.Model):
    name = models.CharField(max_length=100, db_comment="Display name")
    email = models.EmailField(unique=True)
    active = models.BooleanField(default=True)
    token = models.CharField(max_length=40, default=never_called)

    class Meta:
        db_table_comment = "People who write books"
        indexes = [
            models.Index(fields=["-name"], name="author_name_desc"),
            models.Index(Lower("name"), name="author_lower_name"),
            models.Index(fields=["email"], condition=Q(active=True), name="author_active_email"),
        ]
        constraints = [
            models.CheckConstraint(**{CHECK: ~Q(name="")}, name="author_name_present"),
            models.UniqueConstraint(fields=["name"], condition=Q(active=True), name="author_active_name"),
            models.UniqueConstraint(fields=["name", "email"], name="author_name_email"),
        ]


class Profile(models.Model):
    author = models.OneToOneField(Author, on_delete=models.CASCADE, related_name="profile")


class Tag(models.Model):
    label = models.CharField(max_length=20, db_index=True)


class Book(models.Model):
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    editor = models.ForeignKey(Author, on_delete=models.SET_NULL, null=True,
                               db_constraint=False, related_name="+")
    tags = models.ManyToManyField(Tag)
    readers = models.ManyToManyField(Author, through="Reading", related_name="read_books")


class Reading(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    reader = models.ForeignKey(Author, on_delete=models.CASCADE)

    class Meta:
        unique_together = [("book", "reader")]


class Place(models.Model):
    name = models.CharField(max_length=50)


class Restaurant(Place):
    seats = models.IntegerField()


class FeaturedAuthor(Author):
    class Meta:
        proxy = True


class Legacy(models.Model):
    code = models.CharField(max_length=10, primary_key=True)

    class Meta:
        managed = False
        db_table = "legacy_codes"


if hasattr(models, "CompositePrimaryKey"):
    class OrderLine(models.Model):
        pk = models.CompositePrimaryKey("order_id", "line_no")
        order_id = models.IntegerField()
        line_no = models.IntegerField()
