"""Synthetic Django settings; extraction never opens this database."""

INSTALLED_APPS = ["tests.fixtures.django_app"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
