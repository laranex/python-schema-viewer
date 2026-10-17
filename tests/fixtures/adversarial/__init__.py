"""Hostile model modules for the isolation tests. Each tries one side effect while its models are imported and
records what happened in OUTCOME, so tests can tell a blocked attempt from one that never ran. Targets come from
LSV_ADVERSARIAL_* variables the tests pass through; nothing here touches a real service."""
