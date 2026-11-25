# SPDX-License-Identifier: Apache-2.0
"""Write what the code first on the path makes of `world.py`'s Sweep, as canonical JSON:
`PYTHONPATH=<an older commit's tree> python tests/fixtures/old_sweeps/make.py <out.json>`."""

import importlib.util
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("old_sweeps_world", here / "world.py")
assert spec is not None and spec.loader is not None
world = importlib.util.module_from_spec(spec)
spec.loader.exec_module(world)

from othor.ontology.compiler.api import canonical  # noqa: E402

Path(sys.argv[1]).write_text(canonical(world.sweep({})) + "\n")
