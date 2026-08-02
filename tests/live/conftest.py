"""
Blocks real email/SMS alerts from firing during any test in this directory.

Tests that exercise blended_order_manager/blended_position_monitor with
dry_run=False are deliberate -- they need the real fill/exit code paths, not
Kraken's dry-run skip. But that also means notifier._dispatch would send a
real Gmail alert to Jim's phone on every single test run. Blank out the
alert env vars for the duration of the test session so _dispatch's own
"not configured -- skipping" guard kicks in instead.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_real_alerts(monkeypatch):
    monkeypatch.delenv("ALERT_FROM_EMAIL", raising=False)
    monkeypatch.delenv("ALERT_APP_PASSWORD", raising=False)
    monkeypatch.delenv("ALERT_TO_EMAIL", raising=False)
    monkeypatch.delenv("ALERT_TO_SMS", raising=False)
