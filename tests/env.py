"""Pytest plugin loaded early (``-p tests.env``) so env is set before Django settings import."""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
