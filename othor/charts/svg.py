# SPDX-License-Identifier: Apache-2.0
"""render_svg(spec, size) -> SVG text. Pure Python and deterministic: the same spec always
gives the same bytes (part of the replay check). Styling follows board 9c and docs/07.

The renderer draws; it never computes a value. Text labels are formatted by othor.core.fmt.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
from html import escape

from othor.charts.spec import Annotation, ChartSpec, Point, Series, Unit
from othor.charts.tokens import color
from othor.core import fmt

# colour roles (docs/07): positive = accent, negative = neg, neutral = ink; from theme.css
ROLE = {
    "positive": color("accent"),
    "negative": color("neg"),
    "neutral": color("ink"),
    "muted": color("muted-2"),
    "edge": color("ink"),  # just outside its band: dark, with a ring on the last point (M15.3)
}
GRID = color("row")
SHADE = color("surface-2")
TRACK = color("border")
AXIS_TXT = color("muted-2")
INK = color("ink")
WHITE = color("white")
FONT = "Inter, sans-serif"
TEXT_W = 0.58  # Inter's average glyph width, as a share of the font size (label placement)
Box = tuple[float, float, float, float]


def _f(x: float) -> str:
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _value(v: float, unit: Unit, *, sign: bool = False) -> str:
    if unit.type == "percent":
        return fmt.pct(v, decimals=unit.decimals, sign=sign)
    if unit.type == "days":
        return fmt.days(v, decimals=unit.decimals)
    if unit.type == "ms":
        return fmt.ms(v, decimals=unit.decimals)
    if unit.type == "seconds":
        return fmt.seconds(v, decimals=unit.decimals)
    if unit.type == "metres" and unit.scale == "l":
        return fmt.num(v, decimals=unit.decimals) + " L"  # lakh metres (M19.4)
    return fmt.num(v, decimals=unit.decimals, sign=sign)


def _box(x: float, y: float, s: str, size: int, anchor: str) -> Box:
    w = len(s) * size * TEXT_W
    x0 = {"start": x, "middle": x - w / 2, "end": x - w}[anchor]
    return (x0, y - size * 0.8, x0 + w, y + size * 0.2)


def _hit(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class _Svg:
    def __init__(self, w: int, h: int, spec: ChartSpec) -> None:
        self.w, self.h, self.spec, self.parts = w, h, spec, []
        self.taken: list[Box] = []  # labels and marks already placed (M12: no collisions)
        self.labels: list[str] = []  # drawn last, on top of the lines
        self.hits: list[str] = []  # hover targets (v0.25), above everything, pages only

    def add(self, s: str) -> None:
        self.parts.append(s)

    def reserve(self, box: Box) -> None:
        self.taken.append(box)

    def reserve_line(self, xy: list[tuple[float, float]]) -> None:
        """A data line is an obstacle for labels: small boxes every few pixels along it."""
        for (x0, y0), (x1, y1) in itertools.pairwise(xy):
            n = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / 4))
            for i in range(n + 1):
                x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
                self.reserve((x - 1, y - 1.5, x + 1, y + 1.5))

    def label(
        self,
        x: float,
        y: float,
        s: str,
        *,
        size: int = 10,
        fill: str = AXIS_TXT,
        anchor: str = "start",
        weight: int = 400,
        drop: bool = False,
    ) -> bool:
        """A text label that moves out of the way: it keeps inside the chart, then tries
        one line below, one above, two below… until it overlaps nothing placed before it.
        Deterministic: the same spec always places it the same way. `drop`: with no free
        place, it isn't drawn (a value on a line is a help, never a clash; v0.25)."""
        step = size + 2
        best = None
        free = False
        for k in (0, 1, -1, 2, -2, 3, -3, 4):
            yy = y + k * step
            b = _box(x, yy, s, size, anchor)
            dx = max(0.0, 1 - b[0]) - max(0.0, b[2] - (self.w - 1))
            dy = max(0.0, 1 - b[1]) - max(0.0, b[3] - (self.h - 1))
            b = (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)
            if best is None:
                best = (x + dx, yy + dy, b)
            if not any(_hit(b, t) for t in self.taken):
                best = (x + dx, yy + dy, b)
                free = True
                break
        assert best is not None
        if drop and not free:
            return False
        bx, by, b = best
        self.reserve(b)
        n = len(self.parts)
        self.text(bx, by, s, size=size, fill=fill, anchor=anchor, weight=weight)
        self.labels.append(self.parts.pop(n))
        return True

    def text(
        self,
        x: float,
        y: float,
        s: str,
        *,
        size: int = 10,
        fill: str = AXIS_TXT,
        anchor: str = "start",
        weight: int = 400,
    ) -> None:
        self.add(
            f'<text x="{_f(x)}" y="{_f(y)}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{escape(s)}</text>'
        )

    def done(self) -> str:
        alt = escape(self.spec.alt_text)
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{alt}" '
            f'font-family="{FONT}" data-chart="{escape(self.spec.id)}">'
            f"<title>{alt}</title>"
        )
        return head + "".join(self.parts) + "".join(self.labels) + "".join(self.hits) + "</svg>"


def _caption(svg: _Svg, x: float, y: float, unit: Unit) -> None:
    """The axis names its money unit once ("₹ Cr", "$ M"), as the line charts do (9c, M12b)."""
    if unit.type != "currency" or unit.scale not in ("cr", "m", "b", "l", "k"):
        return
    sym = fmt.SYMBOL.get(unit.code or "", fmt.currency_symbol())
    word = {"cr": " Cr", "m": " M", "b": " B", "l": " L", "k": " K"}[unit.scale]
    svg.label(x, y, f"{sym}{word}", size=9)


def _finer(unit: Unit) -> Unit:
    """One decimal more than the unit's own (small shares: 0.8%, not 1%)."""
    return dataclasses.replace(unit, decimals=max(unit.decimals, 0) + 1)


def _fit(s: str, size: int, room: float) -> str:
    """A name shortened with an ellipsis to fit `room` pixels (a long product name)."""
    most = max(int(room / (size * TEXT_W)), 4)
    return s if len(s) <= most else s[: most - 1].rstrip() + "…"


def _ord(d: str) -> int:
    return dt.date.fromisoformat(d).toordinal()


class _Scale:
    def __init__(self, lo: float, hi: float, a: float, b: float) -> None:
        self.lo, self.hi, self.a, self.b = lo, hi if hi != lo else lo + 1, a, b

    def __call__(self, v: float) -> float:
        return self.a + (v - self.lo) / (self.hi - self.lo) * (self.b - self.a)


def _bounds(spec: ChartSpec) -> tuple[float, float]:
    vals = [v for s in spec.series for _, v in s.points]
    if spec.band:
        vals += [v for _, v in spec.band.lower] + [v for _, v in spec.band.upper]
    vals += [a.value for a in spec.annotations if a.type == "threshold" and a.value is not None]
    lo, hi = min(vals), max(vals)
    if spec.y.zero_based or spec.y.zero_line:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    pad = (hi - lo) * 0.08 or 1.0
    return lo - (0 if spec.y.zero_based else pad), hi + pad


def _poly(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{_f(x)},{_f(y)}" for x, y in points)


def _band(svg: _Svg, spec: ChartSpec, xs: _Scale, ys: _Scale, *, legend: bool = False) -> None:
    if not spec.band:
        return
    b = spec.band
    colour, opacity = {
        "negative": (ROLE["negative"], "0.12"),
        # What's ahead (owner, 16 Nov 2026): the likely range in the range bar's cyan
        "positive": (ROLE["positive"], "0.22"),
    }.get(b.role, (ROLE["muted"], "0.18"))
    up = [(xs(_ord(d)), ys(v)) for d, v in b.upper]
    low = [(xs(_ord(d)), ys(v)) for d, v in reversed(b.lower)]
    if fmt.charts().smooth and len(up) >= 3 and len(low) >= 3:  # its edges curve as the line does
        d = _smooth(up) + " L" + _smooth(low)[1:] + " Z"
        svg.add(f'<path d="{d}" fill="{colour}" fill-opacity="{opacity}"/>')
    else:
        svg.add(f'<polygon points="{_poly(up + low)}" fill="{colour}" fill-opacity="{opacity}"/>')
    if b.label and spec.kind != "sparkline" and not legend:
        x, y = up[-1]
        svg.label(x - 2, y - 5, b.label, fill=colour, anchor="end")


def _spark_bars(spec: ChartSpec, w: int, h: int) -> str:
    """One bar per point from a zero baseline (M15.8, the AI usage card); a zero is a hairline,
    so every day shows."""
    svg = _Svg(w, h, spec)
    pts = list(spec.series[0].points)
    top = max((v for _, v in pts), default=0.0) or 1.0
    gap = 2.0
    bw = (w - gap * (len(pts) - 1)) / max(len(pts), 1)
    colour = ROLE[spec.series[0].role]
    for i, (_d, v) in enumerate(pts):
        bh = max(h * v / top, 1.0)
        svg.add(
            f'<rect x="{_f(i * (bw + gap))}" y="{_f(h - bh)}" width="{_f(bw)}" '
            f'height="{_f(bh)}" rx="1" fill="{colour}"/>'
        )
    return svg.done()


XY = tuple[float, float]


def _beziers(xy: list[XY]) -> list[tuple[XY, XY, XY, XY]]:
    """The monotone cubic (Fritsch–Carlson) as Bézier segments: (start, control, control, end)."""
    n = len(xy)
    if n < 3:
        return []
    dx = [xy[i + 1][0] - xy[i][0] for i in range(n - 1)]
    d = [(xy[i + 1][1] - xy[i][1]) / dx[i] if dx[i] else 0.0 for i in range(n - 1)]
    m = [
        d[0],
        *[0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)],
        d[-1],
    ]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        r = a * a + b * b
        if r > 9:
            t = 3 / r**0.5
            m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    segs = []
    for i in range(n - 1):
        (x0, y0), (x1, y1) = xy[i], xy[i + 1]
        h3 = dx[i] / 3
        segs.append(((x0, y0), (x0 + h3, y0 + m[i] * h3), (x1 - h3, y1 - m[i + 1] * h3), (x1, y1)))
    return segs


def _smooth(xy: list[XY]) -> str:
    """A monotone cubic path through every point (Fritsch–Carlson): it passes through each value
    and never overshoots between two of them, so the curve shows no value that isn't there."""
    segs = _beziers(xy)
    if not segs:
        return "M " + " L ".join(f"{_f(x)},{_f(y)}" for x, y in xy)
    out = [f"M {_f(xy[0][0])},{_f(xy[0][1])}"]
    for _p0, c1, c2, p1 in segs:
        out.append(f"C {_f(c1[0])},{_f(c1[1])} {_f(c2[0])},{_f(c2[1])} {_f(p1[0])},{_f(p1[1])}")
    return " ".join(out)


def _sampled(xy: list[XY]) -> list[XY]:
    """Points along the drawn curve, so labels keep off what is drawn, not the straight line."""
    segs = _beziers(xy)
    if not segs:
        return xy
    out = [xy[0]]
    for p0, c1, c2, p1 in segs:
        for k in range(1, 9):
            u = k / 8
            a, b, c, d = (1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u**2, u**3
            out.append(
                (
                    a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0],
                    a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1],
                )
            )
    return out


def _trace(
    svg: _Svg,
    xy: list[tuple[float, float]],
    colour: str,
    smooth: bool,
    width: str,
    dotted: bool = False,
) -> None:
    if smooth or dotted:
        dash = ' stroke-dasharray="1 5"' if dotted else ""
        svg.add(
            f'<path d="{_smooth(xy)}" fill="none" stroke="{colour}" stroke-width="{width}" '
            f'stroke-linejoin="round" stroke-linecap="round"{dash}/>'
        )
    else:
        svg.add(
            f'<polyline points="{_poly(xy)}" fill="none" stroke="{colour}" '
            f'stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"/>'
        )


def _sparkline(spec: ChartSpec, w: int, h: int) -> str:
    if spec.series and spec.series[0].style == "bar":
        return _spark_bars(spec, w, h)
    svg = _Svg(w, h, spec)
    pts = list(spec.series[0].points)
    lo, hi = _bounds(spec)
    xs = _Scale(_ord(pts[0][0]), _ord(pts[-1][0]), 3, w - 4)
    ys = _Scale(lo, hi, h - 3, 3)
    _band(svg, spec, xs, ys)
    if spec.y.zero_line:
        _zero(svg, ys(0.0), 0, w)
    # a muted comparison behind the line (Summary, M16.3), on the same dates
    for other in spec.series[1:]:
        if other.role == "muted" and other.points:
            oxy = [(xs(_ord(d)), ys(v)) for d, v in other.points]
            _trace(svg, oxy, ROLE["muted"], _smooth_on(other), "1.5", other.style == "dotted")
    colour = ROLE[spec.series[0].role]
    xy = [(xs(_ord(d)), ys(v)) for d, v in pts]
    _trace(svg, xy, colour, _smooth_on(spec.series[0]), "2")
    _hover(svg, spec, spec.series[0], xy, r=4)
    x, y = xy[-1]
    if spec.series[0].role == "edge":
        svg.add(
            f'<circle cx="{_f(x)}" cy="{_f(y)}" r="5" fill="none" stroke="{colour}" '
            f'stroke-width="1.5"/>'
        )
    svg.add(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="2.5" fill="{colour}"/>')
    return svg.done()


def _month_ticks(points: list[Point]) -> list[tuple[int, str]]:
    seen: list[tuple[int, str]] = []
    for d, _ in points:
        day = dt.date.fromisoformat(d)
        label = f"{day:%b}"
        if not seen or seen[-1][1] != label:
            seen.append((day.toordinal(), label))
    return seen


def _week_ticks(points: list[Point]) -> list[tuple[int, str]]:
    """Dated ticks for a chart over days, labelled "27 Oct": a week apart over three weeks or
    more, else every second day (a few days: every day), so a short history still reads."""
    if not points:
        return []
    first = dt.date.fromisoformat(points[0][0])
    last = dt.date.fromisoformat(points[-1][0])
    span = (last - first).days
    step = 7 if span >= 21 else (2 if span >= 6 else 1)
    out = []
    day = first
    while day <= last:
        out.append((day.toordinal(), f"{day.day} {day:%b}"))
        day += dt.timedelta(days=step)
    return out


def _region_labels(svg: _Svg, spec: ChartSpec, xs: _Scale, top: int = 18) -> None:
    for a in spec.annotations:
        if a.type == "region" and a.from_ and a.to and a.label and spec.kind != "gap_block":
            svg.label(
                xs(_ord(a.from_)) + 4, top + 4, a.label, fill=ROLE[a.role] if a.role else AXIS_TXT
            )


def _frame(
    spec: ChartSpec, w: int, h: int, *, right_axis: bool = False
) -> tuple[_Svg, _Scale, _Scale]:
    svg = _Svg(w, h, spec)
    left, right = (8, 34) if right_axis else (34, 8)
    top, bottom = 18, 20
    allpts = [p for s in spec.series for p in s.points]
    if spec.band:  # 0.29: a forecast band reaches past the last actual (what's ahead)
        allpts += [p for p in spec.band.upper if _ord(p[0]) > max(_ord(d) for d, _ in allpts)]
    xs = _Scale(min(_ord(d) for d, _ in allpts), max(_ord(d) for d, _ in allpts), left, w - right)
    lo, hi = _bounds(spec)
    ys = _Scale(lo, hi, h - bottom, top)
    # regions first (behind everything)
    for a in spec.annotations:
        if a.type == "region" and a.from_ and a.to and spec.kind != "gap_block":
            x0, x1 = xs(_ord(a.from_)), min(xs(_ord(a.to)), w - right)
            svg.add(
                f'<rect x="{_f(x0)}" y="{top - 6}" width="{_f(x1 - x0)}" '
                f'height="{_f(h - bottom - top + 6)}" fill="{SHADE}"/>'
            )
    # three gridlines at most, at round values, labelled; whole numbers print without decimals
    ticks = _nice_ticks(lo, hi)
    tick_unit = spec.unit
    if ticks and all(float(v).is_integer() for v in ticks):
        tick_unit = _whole(spec.unit)
    for v in ticks:
        y = ys(v)
        svg.add(f'<line x1="{left}" x2="{w - right}" y1="{_f(y)}" y2="{_f(y)}" stroke="{GRID}"/>')
        txt = _value(v, tick_unit, sign=spec.y.zero_line and v != 0)
        x, anchor = (w - 2, "end") if right_axis else (left - 6, "end")
        svg.reserve(_box(x, y + 3, txt, 10, anchor))
        svg.text(x, y + 3, txt, anchor=anchor)
    if (
        ticks
        and spec.unit.type == "currency"
        and spec.unit.scale in ("cr", "m", "b")
        and not right_axis
    ):
        # the axis names its unit once, above the top tick (9c, M12b); the chart's own currency
        sym = fmt.SYMBOL.get(spec.unit.code or "", fmt.currency_symbol())
        word = {"cr": " Cr", "m": " M", "b": " B"}[spec.unit.scale]
        svg.label(1, max(ys(max(ticks)) - 9, 9), f"{sym}{word}", size=9)
    svg.reserve((0.0, h - 14.0, float(w), float(h)))  # the month row: no label lands on it (9c)
    prev: Box | None = None
    # 0.28.5: a step chart over days ("Since last week", 30 nights) is dated a week apart
    # ("27 Oct · 3 Nov"); every other chart keeps its month row
    by_day = spec.kind == "step" and spec.period.grain == "day"
    for o, label in (_week_ticks if by_day else _month_ticks)(sorted(allpts)):
        x, anchor = xs(o), "start"
        if _box(x, h - 5, label, 10, "start")[2] > w - 1:
            x, anchor = w - 1, "end"  # the last month at the right edge isn't cut off (6a2)
        box = _box(x, h - 5, label, 10, anchor)
        if prev is not None and box[0] < prev[2] + 4:
            continue  # months too close to read apart: every other one (M19.4)
        prev = box
        svg.reserve(box)
        svg.text(x, h - 5, label, anchor=anchor)
    if spec.y.zero_line:
        _zero(svg, ys(0.0), left, w - right)
    return svg, xs, ys


def _zero(svg: _Svg, y: float, x0: float, x1: float) -> None:
    """The faint zero line of a chart of changes (#65): one grey line, no colour either side."""
    svg.add(f'<line x1="{_f(x0)}" x2="{_f(x1)}" y1="{_f(y)}" y2="{_f(y)}" stroke="{TRACK}"/>')


def _whole(unit: Unit) -> Unit:
    return dataclasses.replace(unit, decimals=0)


def _nice_ticks(lo: float, hi: float, most: int = 3) -> list[float]:
    """Round tick values (1, 2, 2.5, 5 × 10^k) inside (lo, hi), at most `most` of them."""
    import math

    span = hi - lo
    raw = span / (most + 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step) * step
    ticks = []
    v = first
    while v < hi - step * 0.01:
        if v > lo + step * 0.01:
            ticks.append(round(v, 10))
        v += step
    while len(ticks) > most:
        ticks = ticks[1::2]
    return ticks


def _points(svg: _Svg, spec: ChartSpec, xs: _Scale, ys: _Scale) -> None:
    for a in spec.annotations:
        if a.type == "point" and a.at and a.value is not None:
            svg.add(
                f'<circle cx="{_f(xs(_ord(a.at)))}" cy="{_f(ys(a.value))}" r="3.5" '
                f'fill="{ROLE[a.role or "negative"]}"/>'
            )


def _vlines(svg: _Svg, spec: ChartSpec, xs: _Scale, h: int) -> None:
    for a in spec.annotations:
        if a.type == "vline" and a.at:
            x = xs(_ord(a.at))
            c = ROLE[a.role or "negative"]
            dash = ' stroke-dasharray="4 3"' if (a.style or "dashed") == "dashed" else ""
            svg.add(
                f'<line x1="{_f(x)}" x2="{_f(x)}" y1="10" y2="{h - 20}" stroke="{c}"'
                f' stroke-width="1"{dash}/>'
            )
            if a.label:
                svg.label(x + 4, 12, a.label, fill=c)


def _thresholds(svg: _Svg, spec: ChartSpec, ys: _Scale, w: int) -> None:
    for a in spec.annotations:
        if a.type == "threshold" and a.value is not None:
            y = ys(a.value)
            svg.add(
                f'<line x1="34" x2="{w - 8}" y1="{_f(y)}" y2="{_f(y)}" stroke="{AXIS_TXT}" '
                f'stroke-dasharray="3 3"/>'
            )
            if a.label:
                svg.label(w - 8, y - 4, a.label, anchor="end")


def _line(spec: ChartSpec, w: int, h: int, *, step: bool = False, legend: bool = False) -> str:
    svg, xs, ys = _frame(spec, w, h, right_axis=step)
    lines = []
    for sr in spec.series:
        xy = [(xs(_ord(d)), ys(v)) for d, v in sr.points]
        if step:
            stepped: list[tuple[float, float]] = []
            for i, (x, y) in enumerate(xy):
                if i:
                    stepped.append((x, stepped[-1][1]))
                stepped.append((x, y))
            xy = stepped
        lines.append((sr, xy))
        svg.reserve_line(_sampled(xy) if not step and _smooth_on(sr) else xy)
    for a in spec.annotations:  # the marked points keep their space: no label on a breach
        if a.type == "point" and a.at and a.value is not None:
            cx, cy = xs(_ord(a.at)), ys(a.value)
            svg.reserve((cx - 5, cy - 5, cx + 5, cy + 5))
    _band(svg, spec, xs, ys, legend=legend)
    _thresholds(svg, spec, ys, w)
    _vlines(svg, spec, xs, h)
    _region_labels(svg, spec, xs)
    for sr, xy in lines:
        c = ROLE[sr.role]
        if not step and (_smooth_on(sr) or sr.style == "dotted"):  # smooth by default (v0.25)
            _trace(svg, xy, c, True, "1.5" if sr.style == "dotted" else "2", sr.style == "dotted")
        else:
            svg.add(
                f'<polyline points="{_poly(xy)}" fill="none" stroke="{c}" stroke-width="2" '
                f'stroke-linejoin="round"/>'
            )
        if not step and not legend:  # label lines directly (9c); Big Picture: a legend above
            x, y = xy[-1]
            ly = y + 14 if y < 34 else y - 6
            # values on lines (v0.25): the last value rides with the line's name
            last = (
                f" · {_value(sr.points[-1][1], spec.unit)}"
                if fmt.charts().values == "key" and sr.points
                else ""
            )
            # with no room for both, the name alone (as before v0.25)
            # then the name alone, sliding left along its line before giving in (v0.25: "FY25
            # (same month)" sat on the other line)
            tries = [(sr.name + last, 0.0)] if last else []
            tries += [(sr.name, -k * 36.0) for k in range(4)]
            if not any(
                svg.label(x - 2 + dx, ly, s, fill=c, anchor="end", size=10, weight=500, drop=True)
                for s, dx in tries
            ):
                svg.label(x - 2, ly, sr.name, fill=c, anchor="end", size=10, weight=500)
        if spec.y.zero_line and xy:  # a chart of changes marks where it ends (#65)
            svg.add(f'<circle cx="{_f(xy[-1][0])}" cy="{_f(xy[-1][1])}" r="3" fill="{c}"/>')
    _points(svg, spec, xs, ys)
    if not step:
        _key_values(svg, spec, lines, h)
        for sr, xy in lines:
            _hover(svg, spec, sr, xy)
    return svg.done()


def _smooth_on(sr: Series) -> bool:
    """A line is drawn smooth when it asks to be (Summary), or by the company's choice (v0.25)."""
    return sr.style == "smooth" or (sr.style == "line" and fmt.charts().smooth)


KEY_PER_LINE, KEY_PER_CHART = 3, 6


def _key_values(svg: _Svg, spec: ChartSpec, lines: list[tuple[Series, list[XY]]], h: int) -> None:
    """Values on lines, Key points (owner, 18 Nov 2026): on each line its last value, a finding's
    own points, its highest and lowest; at most three a line and six a chart, a muted line last;
    each placed where it fits, else left out. A small chart shows the last value only."""
    if fmt.charts().values != "key":
        return
    marked = {a.at for a in spec.annotations if a.type == "point" and a.at}
    if h < 120:  # a small chart: the last value only, with the line's name
        return
    shown = 0
    said: set[tuple[int, str]] = set()  # two lines meeting at one value: said once
    for sr, xy in lines:  # each line's last value rides with its name: never said twice (0.28.4)
        if sr.points and xy:
            said.add((round(xy[-1][0]), _value(sr.points[-1][1], spec.unit)))
    for sr, xy in sorted(lines, key=lambda p: p[0].role == "muted"):
        pts = list(sr.points)
        if not pts or sr.style == "dots":
            continue
        vals = [v for _d, v in pts]
        last = len(pts) - 1
        order = [i for i, (d, _v) in enumerate(pts) if d in marked]
        order += [vals.index(max(vals)), vals.index(min(vals))]
        picked: list[int] = []
        last_text = _value(vals[last], spec.unit)
        for i in order:
            # the last rides with the name; a high or low that reads the same is left out
            if i not in picked and i != last and _value(vals[i], spec.unit) != last_text:
                picked.append(i)
        for i in picked[: KEY_PER_LINE - 1]:
            if shown >= KEY_PER_CHART:
                return
            x, y = xy[i]
            text = _value(vals[i], spec.unit)
            if (round(x), text) in said:
                continue
            above = i != vals.index(min(vals))
            ok = svg.label(
                x,
                y - 7 if above else y + 14,
                text,
                size=10,
                fill=INK,
                anchor="middle",
                weight=500,
                drop=True,
            )
            if ok:
                said.add((round(x), text))
                svg.add(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="2.5" fill="{ROLE[sr.role]}"/>')
                shown += 1


def _when(d: str, monthly: bool) -> str:
    day = dt.date.fromisoformat(d)
    return f"{day:%b %Y}" if monthly else f"{day.day} {day:%b %Y}"


def _tip(v: float, unit: Unit) -> str:
    """A hover's value with its unit ("₹4.52 Cr"), the axis says the unit only once."""
    s = _value(v, unit)
    if unit.type == "currency" and unit.scale in ("cr", "m", "b", "l", "k"):
        sym = fmt.SYMBOL.get(unit.code or "", fmt.currency_symbol())
        word = {"cr": " Cr", "m": " M", "b": " B", "l": " L", "k": " K"}[unit.scale]
        return f"{sym}{s}{word}"
    return s


def _hover(svg: _Svg, spec: ChartSpec, sr: Series, xy: list[XY], r: float = 7) -> None:
    """Native hover (owner, 18 Nov 2026): a see-through target on each point with its date and
    value; pages only, never a picture (an email's PNG can't hover)."""
    if not fmt.charts().hover or sr.style == "dots":
        return
    pts = list(sr.points)
    monthly = all(dt.date.fromisoformat(d).day == 1 for d, _v in pts)
    for (d, v), (x, y) in zip(pts, xy, strict=False):
        tip = f"{sr.name} · {_when(d, monthly)} · {_tip(v, spec.unit)}"
        svg.hits.append(
            f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(r)}" fill="transparent">'
            f"<title>{escape(tip)}</title></circle>"
        )


def _contribution(spec: ChartSpec, w: int, h: int) -> str:
    svg = _Svg(w, h, spec)
    cats = spec.categories
    label_w, pad = 56, 44
    lo = min(0.0, *[c.value for c in cats])
    hi = max(0.0, *[c.value for c in cats])
    xs = _Scale(lo, hi, label_w + pad, w - pad)
    row = h / len(cats)
    x0 = xs(0)
    svg.add(f'<line x1="{_f(x0)}" x2="{_f(x0)}" y1="4" y2="{_f(h - 4)}" stroke="{AXIS_TXT}"/>')
    for i, c in enumerate(cats):
        cy = row * i + row / 2
        bh = 18.0
        x1 = xs(c.value)
        left, width = min(x0, x1), max(abs(x1 - x0), 1.0)
        svg.add(
            f'<rect x="{_f(left)}" y="{_f(cy - bh / 2)}" width="{_f(width)}" '
            f'height="{_f(bh)}" fill="{ROLE[c.role]}"/>'
        )
        svg.text(4, cy + 4, c.key, size=11, fill=INK)
        txt = _value(c.value, spec.unit, sign=True)
        inside = width > 48 and c.role == "negative"
        if inside:
            svg.text(left + 6, cy + 4, txt, size=11, fill=WHITE, weight=600)
        elif c.value < 0:
            svg.text(left - 6, cy + 4, txt, size=11, fill=INK, anchor="end", weight=600)
        else:
            svg.text(left + width + 6, cy + 4, txt, size=11, fill=INK, weight=600)
    return svg.done()


def _waterfall(spec: ChartSpec, w: int, h: int) -> str:
    """A total, each part's move from the running level, then the next total (M15.3). The axis
    starts below the lowest level so the totals keep their height; the chart says where."""
    svg = _Svg(w, h, spec)
    cats = spec.categories
    spans: list[tuple[float, float]] = []
    run = 0.0
    for c in cats:
        if c.total:
            spans.append((0.0, c.value))
            run = c.value
        else:
            spans.append((run, run + c.value))
            run += c.value
    levels = [y for a, b in spans for y in (a, b) if not (a == 0.0 and y == 0.0)]
    lo, hi = min(levels), max(levels)
    base = max(0.0, lo - (hi - lo) * 0.6) if hi > lo else 0.0
    if spec.y.zero_based:  # 0.29: bars from zero (a dashboard's roll-forward)
        base = 0.0
    # room above the tallest bar for its value, the unit and the axis note, so none overlap
    ys = _Scale(base, hi, h - 26, 34 if base > 0 else 22)
    # the unit first at the left, then the axis note at the right (it moves down, never over the
    # caption); both before the bars, so the values move out of their way (v0.25)
    _caption(svg, 2, 11, spec.unit)
    if base > 0:
        svg.label(
            w - 4, 11, "axis from " + _value(base, spec.unit), size=10, fill=AXIS_TXT, anchor="end"
        )
    col = (w - 8) / len(cats)
    bw = col * 0.6
    prev_top: float | None = None
    for i, (c, (a, b)) in enumerate(zip(cats, spans, strict=True)):
        x = 4 + col * i + (col - bw) / 2
        y0, y1 = ys(max(a, base)), ys(max(b, base))
        top, height = min(y0, y1), max(abs(y1 - y0), 1.0)
        role = "neutral" if c.total else c.role
        svg.add(
            f'<rect x="{_f(x)}" y="{_f(top)}" width="{_f(bw)}" height="{_f(height)}" '
            f'fill="{ROLE[role]}"/>'
        )
        if prev_top is not None:
            yl = _f(ys(b if c.total else a))
            svg.add(
                f'<line x1="{_f(x - (col - bw))}" x2="{_f(x)}" y1="{yl}" y2="{yl}" '
                f'stroke="{AXIS_TXT}" stroke-dasharray="2 2"/>'
            )
        prev_top = top
        txt = _value(c.value, spec.unit, sign=not c.total)
        svg.reserve((x, top, x + bw, top + height))
        svg.label(x + bw / 2, top - 6, txt, size=11, fill=INK, anchor="middle", weight=600)
        svg.text(x + bw / 2, h - 8, _fit(c.key, 11, col - 4), size=11, fill=INK, anchor="middle")
    return svg.done()


def _share(spec: ChartSpec, w: int, h: int) -> str:
    svg = _Svg(w, h, spec)
    label_w = 68
    row = h / len(spec.categories)
    track_w = w - label_w - 4
    for i, c in enumerate(spec.categories):
        cy = row * i + row / 2
        bh = 26.0
        svg.text(0, cy + 4, c.key, size=11, fill=INK)
        svg.add(
            f'<rect x="{label_w}" y="{_f(cy - bh / 2)}" width="{_f(track_w)}" '
            f'height="{_f(bh)}" fill="{TRACK}"/>'
        )
        svg.add(
            f'<rect x="{label_w}" y="{_f(cy - bh / 2)}" width="{_f(track_w * c.value)}" '
            f'height="{_f(bh)}" fill="{ROLE[c.role]}"/>'
        )
        txt = c.label or _value(c.value, spec.unit)
        fill = WHITE if c.role == "neutral" else INK
        svg.text(label_w + 8, cy + 4, txt, size=11, fill=fill, weight=600)
    return svg.done()


def _gap(spec: ChartSpec, w: int, h: int) -> str:
    svg = _Svg(w, h, spec)
    pts = list(spec.series[0].points)
    region = next((a for a in spec.annotations if a.type == "region"), None)
    # the dashed box takes as many bar slots as its label needs (at least two)
    box_slots = 0
    if region:
        need = len(region.label or "") * 10 * TEXT_W + 16
        box_slots = 2
        while (w - 8) / (len(pts) + box_slots) * box_slots - 8 < need and box_slots < len(pts):
            box_slots += 1
    slots = len(pts) + box_slots
    slot = (w - 8) / slots
    top, bottom = 8, 20
    hi = max(v for _, v in pts) * 1.05
    ys = _Scale(0, hi, h - bottom, top)
    for i, (_d, v) in enumerate(pts):
        x = 4 + slot * i + slot * 0.18
        svg.add(
            f'<rect x="{_f(x)}" y="{_f(ys(v))}" width="{_f(slot * 0.64)}" '
            f'height="{_f(ys(0) - ys(v))}" fill="{ROLE[spec.series[0].role]}"/>'
        )
    for o, label in _month_ticks(pts):
        i = next(k for k, (d, _) in enumerate(pts) if _ord(d) >= o)
        svg.text(4 + slot * i + slot * 0.18, h - 5, label)
    if region:
        _gap_box(svg, region, 4 + slot * len(pts) + 4, top, slot * box_slots - 8, h - bottom - top)
    return svg.done()


def _gap_box(svg: _Svg, a: Annotation, x: float, y: float, w: float, h: float) -> None:
    svg.add(
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" fill="none" '
        f'stroke="{AXIS_TXT}" stroke-dasharray="4 3"/>'
    )
    svg.text(x + w / 2, y + h / 2, "?", size=16, anchor="middle")
    if a.label:
        svg.text(x + w / 2, y + h / 2 + 16, a.label, anchor="middle")


def _dumbbell(spec: ChartSpec, w: int, h: int) -> str:
    """Each category a row: the first series a hollow dot, the second a filled dot, joined; the
    second's value labelled after the row. A threshold annotation is a dashed vertical line.
    Narrower than 480 px, each row's name sits above its dots (phones)."""
    svg = _Svg(w, h, spec)
    a, b = spec.series
    rows = list(zip(a.points, b.points, strict=True))
    size, bottom, top = 13, 26, 24
    # a phone stacks each name over its row when every row has room for both (#70); else the
    # names stay on the left, shortened to fit
    stacked = w < 480 and (h - top - bottom) / max(len(rows), 1) >= 28
    # no more rows than fit (14 px each); the analysis says how many it left out
    most = max(int((h - top - bottom) / 14), 1)
    rows = rows[:most]
    # a stacked name sits over its row and has the width; a name on the left shares it
    room = w - 32 if stacked else w * (0.32 if w >= 480 else 0.3)
    names = {k: _fit(k, size, room) for k, _ in a.points}
    label_w = 16.0 if stacked else max(len(names[k]) for (k, _), _b in rows) * size * TEXT_W + 20
    vals = [v for s in spec.series for _, v in s.points]
    vals += [x.value for x in spec.annotations if x.type == "threshold" and x.value is not None]
    hi = max(vals) * 1.04
    ticks = [0.0, *_nice_ticks(0, hi, most=3 if stacked else 5)]
    hi = max(hi, ticks[-1])
    xs = _Scale(0, hi, label_w, w - 64)
    row = (h - top - bottom) / len(rows)
    # whole numbers on the axis, unless they would repeat (shares under 1%: #70)
    tick_unit = _whole(spec.unit)
    if len({_value(t, tick_unit) for t in ticks}) < len(ticks):
        tick_unit = _finer(spec.unit)
    # small shares that round alike get a decimal (0.8%, 1.2%, not 1%, 1%); other units are
    # shown as they are (two pages can both take 46 ms)
    value_unit = spec.unit
    shown = [_value(vb, value_unit) for (_k, _va), (_k2, vb) in rows]
    if spec.unit.type == "percent" and (
        len(set(shown)) < len(shown) or any(s in ("0%", "1%") for s in shown)
    ):
        value_unit = _finer(spec.unit)
    for t in ticks:
        x = xs(t)
        svg.add(
            f'<line x1="{_f(x)}" x2="{_f(x)}" y1="{top - 8}" y2="{h - bottom}" stroke="{GRID}"/>'
        )
        svg.text(x, h - 8, _value(t, tick_unit), size=11, anchor="middle")
    for x_ in spec.annotations:
        if x_.type == "threshold" and x_.value is not None:
            x = xs(x_.value)
            svg.add(
                f'<line x1="{_f(x)}" x2="{_f(x)}" y1="{top - 8}" y2="{h - bottom}" '
                f'stroke="{INK}" stroke-dasharray="3 3"/>'
            )
            if x_.label:
                svg.text(x - 5, top - 12, x_.label, size=11, fill=INK, anchor="end")
    ca, cb = ROLE[a.role], ROLE[b.role]
    for i, ((key, va), (_, vb)) in enumerate(rows):
        mid = top + row * i + row / 2
        cy = mid + 8 if stacked else mid
        xa, xb = xs(va), xs(vb)
        if stacked:
            svg.text(label_w, mid - 6, names[key], size=size, fill=INK)
        else:
            svg.text(0, cy + 4.5, names[key], size=size, fill=INK)
        svg.add(
            f'<line x1="{_f(min(xa, xb))}" x2="{_f(max(xa, xb))}" y1="{_f(cy)}" y2="{_f(cy)}" '
            f'stroke="{cb}" stroke-opacity="0.35" stroke-width="4" stroke-linecap="round"/>'
        )
        svg.add(
            f'<circle cx="{_f(xa)}" cy="{_f(cy)}" r="5" fill="{WHITE}" stroke="{ca}" '
            f'stroke-width="2"/>'
        )
        svg.add(f'<circle cx="{_f(xb)}" cy="{_f(cy)}" r="5" fill="{cb}"/>')
        svg.reserve((min(xa, xb) - 5, cy - 5, max(xa, xb) + 5, cy + 5))
        svg.label(max(xa, xb) + 10, cy + 4.5, _value(vb, value_unit), size=12, fill=INK, weight=600)
    return svg.done()


def _scatter(spec: ChartSpec, w: int, h: int) -> str:
    """Values by category (Look back's value per customer, 17 Nov 2026): each category a column,
    its points spread across it in value order (no randomness: the same bytes every time), faint
    in the series' colour; a point that a `point` annotation names is drawn dark in its place,
    with its label beside it (an outlier named). The value axis starts at zero."""
    svg = _Svg(w, h, spec)
    pts = [p for s in spec.series for p in s.points]
    cats = list(dict.fromkeys(k for k, _ in pts))
    marks = {(x.at, x.value): x.label for x in spec.annotations if x.type == "point"}
    vals = [v for _, v in pts]
    top, bottom, left, right = 22, 26, 64, 12
    hi = max(vals or [1.0]) * 1.08
    ticks = sorted({0.0, *_nice_ticks(0, hi, most=4)})
    hi = max(hi, ticks[-1])
    ys = _Scale(0, hi, h - bottom, top)
    whole = all(float(t).is_integer() for t in ticks)
    for t in ticks:
        y = ys(t)
        svg.add(f'<line x1="{left}" x2="{w - right}" y1="{_f(y)}" y2="{_f(y)}" stroke="{GRID}"/>')
        svg.text(
            left - 8,
            y + 4,
            _value(t, _whole(spec.unit) if whole else spec.unit),
            size=11,
            anchor="end",
        )
    _caption(svg, 2, 10, spec.unit)
    col = (w - left - right) / max(len(cats), 1)
    colour = ROLE[spec.series[0].role] if spec.series else INK
    named: list[tuple[float, float, str]] = []
    for i, key in enumerate(cats):
        x0 = left + col * i
        svg.text(x0 + col / 2, h - 8, _fit(key, 12, col - 6), size=12, fill=INK, anchor="middle")
        mine = sorted(v for k, v in pts if k == key)
        n = len(mine)
        for j, v in enumerate(mine):
            x = x0 + col * (0.25 + 0.5 * (j / (n - 1) if n > 1 else 0.5))
            label = marks.get((key, v))
            if label is not None:
                named.append((x, ys(v), label))
                continue
            svg.add(
                f'<circle cx="{_f(x)}" cy="{_f(ys(v))}" r="4" fill="{colour}" fill-opacity="0.35"/>'
            )
    for x, y, _label in named:  # drawn last, on top
        svg.add(
            f'<circle cx="{_f(x)}" cy="{_f(y)}" r="5" fill="{INK}" stroke="{WHITE}" '
            'stroke-width="1.5"/>'
        )
        svg.reserve((x - 5, y - 5, x + 5, y + 5))
    for x, y, label in sorted(named, key=lambda n: (n[1], n[0])):  # the highest first
        if label:  # kept inside the chart and clear of the others (label() moves it)
            anchor = "end" if x > w * 0.75 else "start"
            svg.label(x + (-8 if anchor == "end" else 8), y + 4, label, size=11, fill=INK,
                      anchor=anchor, weight=600)  # fmt: skip
    return svg.done()


def _range_bar(spec: ChartSpec, w: int, h: int) -> str:
    """A forecast's likely range (M19.5): a grey track, the range in the positive colour, the
    expected value as a dark dot with a white ring, the goal as a dashed line labelled above, the
    range's ends labelled below. Every label is the computed text the categories carry."""
    svg = _Svg(w, h, spec)
    cat = {c.key: c for c in spec.categories}
    lo, hi, mid = cat["low"], cat["high"], cat["expected"]
    goal = next(
        (a for a in spec.annotations if a.type == "threshold" and a.value is not None), None
    )
    vals = [lo.value, hi.value, mid.value] + (
        [goal.value] if goal and goal.value is not None else []
    )
    a, b = min(vals), max(vals)
    pad = (b - a) * 0.12 or abs(a) * 0.05 or 1.0
    xs = _Scale(a - pad, b + pad, 8, w - 8)
    ty = max(h / 2 - 2, 30.0)  # room for the goal's label above the track
    svg.add(f'<rect x="8" y="{_f(ty - 4)}" width="{w - 16}" height="8" rx="4" fill="{GRID}"/>')
    x0, x1 = xs(lo.value), xs(hi.value)
    svg.add(
        f'<rect x="{_f(x0)}" y="{_f(ty - 4)}" width="{_f(max(x1 - x0, 2))}" height="8" rx="4" '
        f'fill="{ROLE["positive"]}" fill-opacity="0.45"/>'
    )
    if goal is not None and goal.value is not None:
        gx = xs(goal.value)
        c = ROLE["negative"]
        svg.add(
            f'<line x1="{_f(gx)}" x2="{_f(gx)}" y1="{_f(ty - 16)}" y2="{_f(ty + 16)}" '
            f'stroke="{c}" stroke-width="1.5" stroke-dasharray="3 2"/>'
        )
        if goal.label:
            svg.text(gx, ty - 20, goal.label, size=11, fill=c, anchor="middle", weight=600)
    mx = xs(mid.value)
    svg.add(
        f'<circle cx="{_f(mx)}" cy="{_f(ty)}" r="6" fill="{INK}" stroke="{WHITE}" '
        'stroke-width="2"/>'
    )
    svg.text(x0, h - 4, lo.label or "", size=11, anchor="start")
    svg.text(x1, h - 4, hi.label or "", size=11, anchor="end")
    return svg.done()


def _shades(spec: ChartSpec) -> list[tuple[str, str]]:
    """Each series' colour and opacity: its role's colour, lighter for the earlier series of the
    same role (receivables by age: two light-to-strong cyans, then two light-to-strong reds)."""
    out = []
    for i, sr in enumerate(spec.series):
        same = [x for x in spec.series if x.role == sr.role]
        k = sum(1 for x in spec.series[:i] if x.role == sr.role)
        out.append((ROLE[sr.role], _f(0.4 + 0.6 * (k + 1) / len(same)) if len(same) > 1 else "1"))
    return out


def _stacked_share(spec: ChartSpec, w: int, h: int) -> str:
    """0.29: each period's parts stacked to 100%, bottom first (receivables by age, month ends).
    The shares are computed (they add to 1 by largest remainder); a `point` annotation's label
    sits above its period's bar in its role's colour."""
    svg = _Svg(w, h, spec)
    periods = [d for d, _ in spec.series[0].points]
    top, bottom = 18, 20
    col = (w - 8) / max(len(periods), 1)
    bw = col * 0.64
    shades = _shades(spec)
    for i, d in enumerate(periods):
        x = 4 + col * i + (col - bw) / 2
        acc = 0.0
        for sr, (c, o) in zip(spec.series, shades, strict=True):
            v = dict(sr.points).get(d, 0.0)
            hh = (h - top - bottom) * v
            y = h - bottom - acc - hh
            svg.add(
                f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(bw)}" height="{_f(hh)}" fill="{c}" '
                f'fill-opacity="{o}"/>'
            )
            acc += hh
    prev: Box | None = None
    for i, d in reversed(list(enumerate(periods))):  # the last month always; then every other
        x = 4 + col * i + col / 2
        label = f"{dt.date.fromisoformat(d):%b}"
        box = _box(x, h - 5, label, 10, "middle")
        if prev is not None and box[2] > prev[0] - 4:
            continue
        prev = box
        svg.text(x, h - 5, label, anchor="middle")
    for a in spec.annotations:
        if a.type == "point" and a.at in periods and a.label:
            i = periods.index(a.at)
            c = ROLE[a.role or "negative"]
            svg.text(
                4 + col * i + col / 2,
                top - 6,
                a.label,
                size=10,
                fill=c,
                anchor="middle",
                weight=600,
            )
    return svg.done()


def _heatmap(spec: ChartSpec, w: int, h: int) -> str:
    """0.29: rows (series, their names the row labels) by columns (the first row's dates), each
    cell darker the larger its value, in the positive colour. Every third column is dated."""
    svg = _Svg(w, h, spec)
    cols = [d for d, _ in spec.series[0].points]
    left, top, gap = 36, 16, 3
    hi = max((v for sr in spec.series for _, v in sr.points), default=0.0) or 1.0
    cw = (w - left) / max(len(cols), 1)
    rh = (h - top) / max(len(spec.series), 1)
    c = ROLE["positive"]
    for j, d in enumerate(cols):
        if j % 3 == 0:
            day = dt.date.fromisoformat(d)
            svg.text(left + cw * j + 1, 11, f"{day.day} {day:%b}", size=10)
    for i, sr in enumerate(spec.series):
        y = top + rh * i
        svg.text(0, y + rh / 2 + 4, sr.name, size=11, fill=INK)
        vals = dict(sr.points)
        for j, d in enumerate(cols):
            v = vals.get(d)
            if v is None:
                continue
            o = _f(0.08 + 0.85 * max(v, 0.0) / hi)
            svg.add(
                f'<rect x="{_f(left + cw * j)}" y="{_f(y)}" width="{_f(cw - gap)}" '
                f'height="{_f(rh - gap)}" rx="4" fill="{c}" fill-opacity="{o}"/>'
            )
    return svg.done()


def _bullet(spec: ChartSpec, w: int, h: int) -> str:
    """0.29: a goal's track (its bad side tinted), the likely range, the heading value as a dark
    dot labelled above, the goal as a line labelled below. Labels are the categories' text."""
    svg = _Svg(w, h, spec)
    cat = {c.key: c for c in spec.categories}
    lo, hi, mid = cat["low"], cat["high"], cat["expected"]
    bad_above = cat["bad_above"].value >= 0.5 if "bad_above" in cat else True
    goal = next(
        (a for a in spec.annotations if a.type == "threshold" and a.value is not None), None
    )
    vals = [lo.value, hi.value, mid.value] + (
        [goal.value] if goal and goal.value is not None else []
    )
    a, b = min(vals), max(vals)
    pad = (b - a) * 0.35 or abs(a) * 0.1 or 1.0
    xs = _Scale(a - pad, b + pad, 4, w - 4)
    ty = h / 2 - 2
    svg.add(f'<rect x="4" y="{_f(ty - 7)}" width="{w - 8}" height="14" rx="3" fill="{GRID}"/>')
    if goal is not None and goal.value is not None:
        gx = xs(goal.value)
        x0, x1 = (gx, w - 4.0) if bad_above else (4.0, gx)
        svg.add(
            f'<rect x="{_f(x0)}" y="{_f(ty - 7)}" width="{_f(max(x1 - x0, 0))}" height="14" '
            f'rx="3" fill="{ROLE["negative"]}" fill-opacity="0.14"/>'
        )
    x0, x1 = xs(lo.value), xs(hi.value)
    svg.add(
        f'<rect x="{_f(x0)}" y="{_f(ty - 3)}" width="{_f(max(x1 - x0, 2))}" height="6" rx="3" '
        f'fill="{ROLE["positive"]}" fill-opacity="0.6"/>'
    )
    if goal is not None and goal.value is not None:
        gx = xs(goal.value)
        svg.add(
            f'<line x1="{_f(gx)}" x2="{_f(gx)}" y1="{_f(ty - 13)}" y2="{_f(ty + 13)}" '
            f'stroke="{ROLE["negative"]}" stroke-width="2"/>'
        )
        if goal.label:
            svg.text(gx, h - 2, goal.label, size=11, fill=ROLE["negative"], anchor="middle")
    mx = xs(mid.value)
    svg.add(f'<circle cx="{_f(mx)}" cy="{_f(ty)}" r="6" fill="{INK}"/>')
    if mid.label:
        svg.text(mx, 9, mid.label, size=11, fill=INK, anchor="middle", weight=600)
    return svg.done()


def _small_multiples(spec: ChartSpec, w: int, h: int) -> str:
    """0.29: one small line per series, all on the same scale, its name at the left and its last
    value at the right; the one to look at (`emphasis`) in its role's colour, the rest muted."""
    svg = _Svg(w, h, spec)
    n = max(len(spec.series), 1)
    panel = h / n
    vals = [v for sr in spec.series for _, v in sr.points]
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
    for i, sr in enumerate(spec.series):
        y0 = panel * i
        c = ROLE[sr.role] if sr.emphasis else ROLE["muted"]
        svg.text(0, y0 + 12, sr.name, size=11, fill=INK)
        if sr.points:
            svg.text(
                w,
                y0 + 12,
                _value(sr.points[-1][1], spec.unit),
                size=11,
                fill=ROLE[sr.role] if sr.emphasis else INK,
                anchor="end",
                weight=600,
            )
        if len(sr.points) < 2:
            continue
        xs = _Scale(0, len(sr.points) - 1, 3, w - 4)
        ys = _Scale(lo, hi, y0 + panel - 4, y0 + 18)
        xy = [(xs(k), ys(v)) for k, (_d, v) in enumerate(sr.points)]
        svg.add(
            f'<polyline points="{_poly(xy)}" fill="none" stroke="{c}" stroke-width="2" '
            f'stroke-linejoin="round"/>'
        )
        svg.add(f'<circle cx="{_f(xy[-1][0])}" cy="{_f(xy[-1][1])}" r="3" fill="{c}"/>')
    return svg.done()


def render_svg(spec: ChartSpec, size: tuple[int, int] = (400, 160), *, legend: bool = False) -> str:
    """`legend`: series names and the band's label go to a legend the page draws above the chart,
    not on the plot (Big Picture, owner 15 Nov 2026; other charts from the typography release)."""
    w, h = size
    if spec.kind == "range_bar":
        return _range_bar(spec, w, h)
    if spec.kind == "sparkline":
        return _sparkline(spec, w, h)
    if spec.kind == "line":
        return _line(spec, w, h, legend=legend)
    if spec.kind == "step":
        return _line(spec, w, h, step=True)
    if spec.kind == "bars_contribution":
        return _contribution(spec, w, h)
    if spec.kind == "bars_share":
        return _share(spec, w, h)
    if spec.kind == "waterfall":
        return _waterfall(spec, w, h)
    if spec.kind == "gap_block":
        return _gap(spec, w, h)
    if spec.kind == "dumbbell":
        return _dumbbell(spec, w, h)
    if spec.kind == "scatter":
        return _scatter(spec, w, h)
    if spec.kind == "stacked_share":
        return _stacked_share(spec, w, h)
    if spec.kind == "heatmap":
        return _heatmap(spec, w, h)
    if spec.kind == "bullet":
        return _bullet(spec, w, h)
    if spec.kind == "small_multiples":
        return _small_multiples(spec, w, h)
    raise NotImplementedError(f"{spec.kind} is rendered from M6")
