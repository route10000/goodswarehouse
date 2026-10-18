import pytest
from django.utils import timezone


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    """Tests run without collectstatic, so use the non-manifest static storage."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture
def pinned_now(monkeypatch):
    """Stop ``timezone.now()`` (and so ``timezone.localdate()``) at the test's start.

    For tests that take «today» themselves and then call code that takes it again: if
    local midnight passes in between, the two dates differ and the test fails at random.
    Returns the pinned instant.
    """
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    return now
