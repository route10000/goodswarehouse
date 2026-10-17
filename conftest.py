import pytest


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    """Tests run without collectstatic, so use the non-manifest static storage."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
