# SPDX-License-Identifier: Apache-2.0
import io

from PIL import Image

from othor.charts.png import render_png
from othor.web.components_demo import demo_specs


def test_png_is_2x_and_deterministic() -> None:
    spec = demo_specs()["bars_contribution"]
    a = render_png(spec, (380, 110))
    assert a == render_png(spec, (380, 110))
    img = Image.open(io.BytesIO(a))
    assert img.size == (760, 220)
    # the negative bar is drawn in the negative role colour
    assert (214, 69, 69) in {img.getpixel((x, 36))[:3] for x in range(0, 760, 4)}
