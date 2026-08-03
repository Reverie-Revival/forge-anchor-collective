"""
Shared fixtures/helpers for tests/live/.

Blocks real email/SMS alerts from firing during any test in this directory:
tests that exercise order_manager/position_monitor with dry_run=False are
deliberate -- they need the real fill/exit code paths, not Kraken's dry-run
skip. But that also means notifier._dispatch would send a real Gmail alert to
Jim's phone on every single test run. Blank out the alert env vars for the
duration of the test session so _dispatch's own "not configured -- skipping"
guard kicks in instead.
"""
import os

import pytest
from sqlalchemy import create_engine


@pytest.fixture(autouse=True)
def _no_real_alerts(monkeypatch):
    monkeypatch.delenv("ALERT_FROM_EMAIL", raising=False)
    monkeypatch.delenv("ALERT_APP_PASSWORD", raising=False)
    monkeypatch.delenv("ALERT_TO_EMAIL", raising=False)
    monkeypatch.delenv("ALERT_TO_SMS", raising=False)


def get_local_engine():
    """Local Postgres connection -- shared by every tests/live/ module so the
    URL-normalization logic only lives in one place."""
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)
