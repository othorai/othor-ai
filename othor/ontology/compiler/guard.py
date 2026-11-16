# SPDX-License-Identifier: Apache-2.0
"""Defence in depth for the personal-column guard: parse every rendered statement back with
sqlglot and refuse it if it names a personal column of a table it reads, or isn't a SELECT."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

if TYPE_CHECKING:
    from othor.ontology.compiler.render import Scope


def recheck(metric: str, sql: str, dialect: str, scope: Scope) -> None:
    from othor.ontology.compiler.render import CompileError, PersonalColumnRejected

    tree = sqlglot.parse_one(sql.replace(":", "__p_") if dialect != "duckdb" else sql, read=dialect)
    if not isinstance(tree, exp.Select | exp.Union):
        raise CompileError(f"{metric}: rendered SQL is not a single SELECT")
    for bad in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Command):
        if tree.find(bad) is not None:
            raise CompileError(f"{metric}: rendered SQL is not read-only")
    tables = {"t": scope.fact, **scope.joined}
    names = {c.name.lower() for c in tree.find_all(exp.Column)}
    for t in tables.values():
        hit = names & t.personal
        if hit:
            raise PersonalColumnRejected(metric, f"{t.schema}.{t.name}", "the rendered SQL")
