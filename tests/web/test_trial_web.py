# SPDX-License-Identifier: Apache-2.0
"""Open sign-up's free trial (owner, 16 Nov 2026): a company that signs itself up gets 14 days;
Home counts them down; 3 days before, one email; at the end, the company is closed as Delete
company closes it (trial words), and purged 30 days later; `othor admin trial` extends or ends
it, and reopens a company its trial closed. Invited companies have no trial."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from typer.testing import CliRunner

from othor import trials
from othor.core.models import AuthSession, Company
from othor.core.tenancy import system_scope
from tests.web.conftest import ROOT, make_env, signup


def _company(env):
    svc = env.app.state.services
    with svc.sf() as s, system_scope("test"):
        return s.scalars(sa.select(Company).where(Company.slug == "indigothread")).one()


def _tick(env):
    svc = env.app.state.services
    return trials.tick(svc.sf, svc.settings, env.relay, svc.auth, env.clock())


def test_open_signup_starts_a_14_day_trial(tmp_path: Path) -> None:
    with make_env(tmp_path, signup="open", trial_days=14, support_email="support@othor.ai") as env:
        page = env.client.get(ROOT + "/signup").text
        assert "14-day free trial" in page
        signup(env)
        c = _company(env)
        assert c.trial_ends_at is not None
        assert (c.trial_ends_at - env.clock()).days == 14
        home = env.client.get(env.host("indigothread") + "/home").text
        assert "Trial · 14 days left" in home and "Talk to us to continue" in home


def test_no_trial_without_the_setting(tmp_path: Path) -> None:
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        assert _company(env).trial_ends_at is None
        assert "Trial ·" not in env.client.get(env.host("indigothread") + "/home").text


def test_reminder_once_then_closed_at_the_end_then_reopened_by_admin(
    tmp_path: Path, monkeypatch
) -> None:
    with make_env(tmp_path, signup="open", trial_days=14, support_email="support@othor.ai") as env:
        signup(env)
        env.clock.tick(days=10)
        assert _tick(env) == 0 and not [m for m in env.relay.outbox if "free trial" in m.subject]
        env.clock.tick(days=1, hours=1)  # 2 days 23 hours left
        _tick(env)
        _tick(env)
        reminders = [m for m in env.relay.outbox if "free trial ends" in m.subject]
        assert len(reminders) == 1 and "support@othor.ai" in reminders[0].text
        assert "Trial · 3 days left" in env.client.get(env.host("indigothread") + "/home").text
        env.clock.tick(days=3)
        assert _tick(env) == 1
        c = _company(env)
        assert c.deleted_at is not None and c.closed_by == "trial"
        assert (c.purge_after - c.deleted_at).days == 30
        assert any("free trial has ended" in m.subject for m in env.relay.outbox)
        svc = env.app.state.services
        with svc.sf() as s:
            live = s.scalar(
                sa.select(sa.func.count())
                .select_from(AuthSession)
                .where(AuthSession.company_id == c.id, AuthSession.revoked_at.is_(None))
            )
        assert live == 0
        assert _tick(env) == 0  # closed once

        # the operator extends it: reopened, 7 more days from now
        from othor import cli

        monkeypatch.setenv("OTHOR_DB_URL", str(svc.settings.db_url))
        monkeypatch.setenv("OTHOR_DATA_DIR", str(svc.settings.data_dir))
        monkeypatch.setattr(cli, "_admin_now", lambda: env.clock())
        from othor.core import config

        config.get_settings.cache_clear()
        refused = CliRunner().invoke(cli.app, ["admin", "reopen", "indigothread", "--yes"])
        assert refused.exit_code == 1 and "admin trial" in refused.output
        ok = CliRunner().invoke(cli.app, ["admin", "trial", "indigothread", "--extend", "7"])
        assert ok.exit_code == 0, ok.output
        c = _company(env)
        assert c.deleted_at is None and (c.trial_ends_at - env.clock()).days == 7
        ok = CliRunner().invoke(cli.app, ["admin", "trial", "indigothread", "--end-trial"])
        assert ok.exit_code == 0 and _company(env).trial_ends_at is None
        config.get_settings.cache_clear()
