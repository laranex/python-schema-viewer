"""Synthetic Tortoise config. The URL is never connected; only its scheme is read."""

TORTOISE_ORM = {
    "connections": {"default": "postgres://example:example@localhost:5432/example"},
    "apps": {"models": {"models": ["tests.fixtures.tortoise_models"], "default_connection": "default"}},
}
