"""Test plugin: register() raises an exception.

This plugin is used by the test suite to verify that plugin failures
are recorded and don't crash the application.
"""


def register():
    raise RuntimeError("This plugin intentionally fails on registration")