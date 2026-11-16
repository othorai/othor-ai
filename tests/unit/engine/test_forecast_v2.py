# SPDX-License-Identifier: Apache-2.0
"""v0.19 (M19.1): the method chosen by its own backtest against a plain baseline, calibrated
P10/P50/P90 from out-of-sample errors, and plain reasons to say nothing. Pure and deterministic."""

from __future__ import annotations

import datetime as dt
import json

import numpy as np

from othor.engine.forecast import abstain, band
from othor.engine.forecast import store as fstore
from othor.engine.forecast.select import BEAT, select


def _weeks(n: int) -> list[dt.date]:
    return [dt.date(2026, 1, 5) + dt.timedelta(weeks=i) for i in range(n)]


def _months(n: int) -> list[dt.date]:
    out, d = [], dt.date(2024, 1, 1)
    for _ in range(n):
        out.append(d)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


# --- the choice --------------------------------------------------------------------------------


def test_a_steady_trend_picks_a_trend_method():
    v = [100.0 + 5 * i + (0.3 if i % 2 else -0.3) for i in range(30)]
    s = select(v, "week")
    assert s is not None and s.method in ("ETS(A,A,N)", "ETS(A,Ad,N)")
    assert s.baseline == "naive" and set(s.scores) >= {"naive", s.method}


def test_pure_noise_falls_back_to_the_baseline():
    r = np.random.default_rng(7)
    v = list(1000 + r.normal(0, 50, 30))
    s = select(v, "week")
    assert s is not None
    base = float(s.scores[s.baseline]["mae"])
    won = float(s.scores[s.method]["mae"])
    # whatever wins beats the baseline by the margin, or it is the baseline
    assert s.method == s.baseline or won <= base * (1 - BEAT) + 1e-9


def test_two_seasonal_years_pick_a_seasonal_method():
    shape = [10, 12, 15, 20, 30, 45, 50, 44, 30, 20, 14, 11]
    v = [100 * x + i * 2 for i, x in enumerate(shape * 3)]
    s = select(v, "month")
    assert s is not None and s.baseline == "seasonal-naive"
    assert s.method in ("ETS(A,N,A)", "ETS(A,Ad,A)", "seasonal-naive")


def test_known_pattern_periods_are_not_scored():
    v = [100.0 + i for i in range(30)]
    v[25] = 900.0  # a Diwali week
    mask = [i == 25 for i in range(30)]
    s_masked = select(v, "week", mask)
    s_open = select(v, "week")
    assert s_masked is not None and s_open is not None
    assert s_masked.scores[s_masked.method]["n"] == s_open.scores[s_open.method]["n"] - 1
    assert float(s_masked.scores[s_masked.method]["mae"]) < float(
        s_open.scores[s_open.method]["mae"]
    )


def test_too_little_history_to_test_returns_none():
    assert select([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], "week") is None


def test_the_choice_is_the_same_every_time():
    r = np.random.default_rng(3)
    v = list(500 + np.cumsum(r.normal(0, 10, 40)))
    a, b = select(v, "week"), select(v, "week")
    assert a == b


# --- the band ----------------------------------------------------------------------------------


def test_sigma_is_the_backtests_error():
    assert band.sigma([3.0, -4.0]) == np.sqrt((9 + 16) / 2)


def test_fat_tailed_misses_widen_the_band_and_it_is_never_narrowed():
    errs = [1.0] * 9 + [8.0, -9.0, 10.0]
    k, inside, n = band.calibrate(errs)
    assert k > 1.0 and inside >= 10 and n == 12
    k2, inside2, _ = band.calibrate([0.1] * 12)
    assert k2 == 1.0 and inside2 == 12  # already covered: never narrowed below 1


def test_the_band_widens_with_the_horizon():
    assert band.half_width(10, 1.2, 4) == 2 * band.half_width(10, 1.2, 1)


# --- saying nothing ----------------------------------------------------------------------------


def test_each_reason_to_say_nothing():
    base = {"weeks": 20, "band_weeks": 8, "stale_after_last": False, "grain": "week"}
    assert abstain.reason(**base, p50=100, lower=90, upper=110) is None
    assert abstain.reason(**{**base, "weeks": 5}, p50=100, lower=90, upper=110) == "short_history"
    assert (
        abstain.reason(**{**base, "stale_after_last": True}, p50=100, lower=90, upper=110)
        == "source_stale"
    )
    assert abstain.reason(**base, p50=100, lower=60, upper=140) == "too_wide"
    assert abstain.reason(**{**base, "grain": "month"}, p50=100, lower=75, upper=125) == "too_wide"


# --- the stored rows ---------------------------------------------------------------------------


def test_rows_carry_the_choice_and_the_calibration():
    p = _weeks(30)
    v = [100.0 + 2 * i + (1 if i % 3 else -1) for i in range(30)]
    got, sel = fstore.forecast_series(p, v, "week", 4, mask=[False] * 30, band=True)
    assert got is not None and sel is not None
    assert got.method == sel.method and got.params["engine"] == 2
    assert got.params["sigma_source"] in ("backtest", "fit")
    assert float(got.params["sigma"]) == max(
        float(got.params["sigma_backtest"]), float(got.params["sigma_fit"])
    )
    assert set(got.params["calibration"]) == {"k", "inside", "of"}
    f1, f4 = got.points[0], got.points[3]
    assert float(f1.lower) < float(f1.point) < float(f1.upper)
    assert float(f4.upper) - float(f4.lower) > float(f1.upper) - float(f1.lower)
    again, _ = fstore.forecast_series(p, v, "week", 4, mask=[False] * 30, band=True)
    assert json.dumps(again.__dict__, default=str) == json.dumps(got.__dict__, default=str)


def test_a_short_series_has_no_v2_forecast():
    """Too little history to test a method: no v2 forecast (the forecast of record still is)."""
    p = _months(9)
    v = [10.0, 11, 12, 11, 13, 14, 13, 15, 16]
    got, sel = fstore.forecast_series(p, v, "month", 1, mask=[False] * 9, band=True)
    assert got is None and sel is None


def test_the_forecast_of_record_is_unchanged_and_v2_rides_alongside():
    """Owner, 15 Nov 2026: Sweep's Forecast breach and the goals keep the forecast of record;
    What's ahead reads `v2`."""
    from othor.engine.forecast.ets import fit

    p = _weeks(30)
    v = [100.0 + 2 * i + (1 if i % 3 else -1) for i in range(30)]
    old = fit(p, v, "week", 1, mask=[False] * 30, band=True)
    got2, _ = fstore.forecast_series(p, v, "week", 1, mask=[False] * 30, band=True)
    assert old is not None and got2 is not None
    row = {
        **fstore._row("m", "week", old.points[0], old.method, old.aicc, old.params, p[-1], False),
        "v2": fstore._v2(got2.points[0]),
    }
    assert row["point"] == old.points[0].point and row["lower"] == old.points[0].lower
    assert row["v2"] == {
        "p10": got2.points[0].lower,
        "p50": got2.points[0].point,
        "p90": got2.points[0].upper,
    }
