"""Safety rails shared by PostgreSQL integration tests."""

import os
import re

import pytest

_DISPOSABLE_DATABASE = re.compile(r"^dental_legal_test_[a-z0-9_]+$")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse to let opt-in integration tests mutate a runtime database."""

    del session
    if os.getenv("POSTGRES_INTEGRATION") != "1":
        return
    database = os.getenv("POSTGRES_DB", "")
    if _DISPOSABLE_DATABASE.fullmatch(database) is None:
        raise pytest.UsageError(
            "POSTGRES_INTEGRATION=1 requires a disposable POSTGRES_DB named "
            "dental_legal_test_<suffix>"
        )
