# SPDX-License-Identifier: Apache-2.0
import pytest

from othor.charts.spec import Category, ChartSpec, Series, SpecError
from othor.web.components_demo import demo_specs


def test_json_round_trip_for_every_demo_spec() -> None:
    for spec in demo_specs().values():
        again = ChartSpec.from_json(spec.to_json())
        assert again == spec
        assert again.to_json() == spec.to_json()


def test_json_uses_from_not_from_() -> None:
    spec = demo_specs()["step"]
    assert '"from"' in spec.to_json() and "from_" not in spec.to_json()


def test_unknown_kind_and_role_rejected() -> None:
    with pytest.raises(SpecError, match="kind"):
        ChartSpec(id="x", kind="pie", title="t", alt_text="a")  # type: ignore[arg-type]
    with pytest.raises(SpecError, match="role"):
        ChartSpec(
            id="x",
            kind="bars_contribution",
            title="t",
            alt_text="a",
            categories=(Category("South", -1.4, "green"),),
        )  # type: ignore[arg-type]


def test_alt_text_required() -> None:
    with pytest.raises(SpecError, match="alt_text"):
        ChartSpec(
            id="x",
            kind="line",
            title="t",
            alt_text="",
            series=(Series("a", "A", "neutral", (("2026-01-01", 1.0),)),),
        )


def test_a_chart_without_a_zero_line_keeps_its_v1_bytes():
    """`y.zero_line` (#65) is written only when set: charts stored before it replay byte for byte
    (CI on a5a1c98: the 1 Nov issue golden changed)."""
    from othor.charts.spec import ChartSpec, Series, YAxis

    c = ChartSpec(
        id="x",
        kind="line",
        title="x",
        alt_text="x",
        series=(Series("a", "a", "neutral", (("2026-01-01", 1.0),)),),
    )
    assert "zero_line" not in c.to_json()
    z = ChartSpec(
        id="x", kind="line", title="x", alt_text="x", y=YAxis(zero_line=True), series=c.series
    )
    assert '"zero_line":true' in z.to_json() and ChartSpec.from_dict(z.to_dict()).y.zero_line
