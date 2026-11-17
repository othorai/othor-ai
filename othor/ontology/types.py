# SPDX-License-Identifier: Apache-2.0
"""Typed Ontology record bodies (docs/03 "Ontology") and the expression AST the compiler reads.

Every record written through `othor.ontology.service.apply` is validated here. Metric
definitions are structured, never SQL: the compiler builds SQL from this AST with sqlglot, so
no text from a record is ever concatenated into a statement.

Expression AST (JSON):
    {"col": "net_amount"}                    a column of the metric's table
    {"col": "region", "table": "customers"}  a column of a joined table (by a join rule)
    {"lit": "X"} · {"lit": 60} · {"lit": "0.9144"} · {"lit": null}
    {"param": "as_of"}                       bound at run time (as_of, period_start, period_end)
    {"metric": "net_revenue", "missing": "zero"}   derived metrics only
    {"op": "+", "args": [a, b]}              + - * / = <> < <= > >= and or not in is_null not_null
    {"fn": "coalesce", "args": [...]}        coalesce · abs · days_between · add_days · date
                                             · days_in_period (derived only, no args)
    {"case": [[cond, value], ...], "else": value}
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OPS = {
    "+",
    "-",
    "*",
    "/",
    "=",
    "<>",
    "<",
    "<=",
    ">",
    ">=",
    "and",
    "or",
    "not",
    "in",
    "is_null",
    "not_null",
}
FNS = {"coalesce": None, "abs": 1, "days_between": 2, "add_days": 2, "date": 1, "days_in_period": 0}
PARAMS = {"as_of", "period_start", "period_end"}
_IDENT_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_ ()₹/.-ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class ExprError(ValueError):
    pass


def check_expr(node: Any, *, derived: bool = False, path: str = "expr") -> None:
    """Raise ExprError for anything the compiler doesn't know. Unknown never passes through."""
    if not isinstance(node, dict) or not node:
        raise ExprError(f"{path}: an expression must be an object")
    if "col" in node:
        extra = set(node) - {"col", "table"}
        if extra or not isinstance(node["col"], str) or not node["col"]:
            raise ExprError(f"{path}: a column is {{'col': name}} (optionally 'table')")
        if not set(node["col"]) <= _IDENT_OK:
            raise ExprError(f"{path}: column name has characters a column can't have")
        return
    if "lit" in node:
        if set(node) != {"lit"} or not isinstance(node["lit"], str | int | bool | None):
            raise ExprError(f"{path}: a literal is a string, integer, boolean or null")
        return
    if "param" in node:
        if set(node) != {"param"} or node["param"] not in PARAMS:
            raise ExprError(f"{path}: parameters are {sorted(PARAMS)}")
        return
    if "metric" in node:
        if not derived:
            raise ExprError(f"{path}: other metrics can be used only in a derived formula")
        if set(node) - {"metric", "missing"} or node.get("missing", "not_determinable") not in (
            "zero",
            "not_determinable",
        ):
            raise ExprError(f"{path}: a metric reference is {{'metric': key, 'missing': …}}")
        return
    if "op" in node:
        if node["op"] not in OPS or not isinstance(node.get("args"), list) or not node["args"]:
            raise ExprError(f"{path}: unknown operator {node.get('op')!r}")
        unary = node["op"] in ("not", "is_null", "not_null")
        if unary != (len(node["args"]) == 1) and node["op"] != "in":
            raise ExprError(f"{path}: wrong number of arguments for {node['op']}")
        for i, a in enumerate(node["args"]):
            check_expr(a, derived=derived, path=f"{path}.{node['op']}[{i}]")
        return
    if "fn" in node:
        fn = node["fn"]
        if fn not in FNS:
            raise ExprError(f"{path}: unknown function {fn!r}")
        args = node.get("args", [])
        n = FNS[fn]
        if (n is not None and len(args) != n) or (n is None and not args):
            raise ExprError(f"{path}: wrong number of arguments for {fn}")
        if fn == "days_in_period" and not derived:
            raise ExprError(f"{path}: days_in_period is for derived formulas")
        for i, a in enumerate(args):
            check_expr(a, derived=derived, path=f"{path}.{fn}[{i}]")
        return
    if "case" in node:
        if not isinstance(node["case"], list) or not node["case"]:
            raise ExprError(f"{path}: case needs [[condition, value], …]")
        for i, pair in enumerate(node["case"]):
            if not isinstance(pair, list) or len(pair) != 2:
                raise ExprError(f"{path}: case branch {i} is [condition, value]")
            check_expr(pair[0], derived=derived, path=f"{path}.case[{i}].when")
            check_expr(pair[1], derived=derived, path=f"{path}.case[{i}].then")
        if "else" in node:
            check_expr(node["else"], derived=derived, path=f"{path}.else")
        return
    raise ExprError(f"{path}: unknown expression {sorted(node)}")


def walk(node: Any) -> list[dict[str, Any]]:
    """Every node of an expression, depth first."""
    out: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        out.append(n)
        for a in n.get("args", []):
            stack.append(a)
        for pair in n.get("case", []):
            stack.extend(pair)
        if "else" in n:
            stack.append(n["else"])
    return out


def _decimal(v: str) -> str:
    try:
        Decimal(v)
    except InvalidOperation as e:
        raise ValueError(f"{v!r} is not a number") from e
    return v


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TableRef(Body):
    """A scanned table by its source's name (erp_db, sales_dw, a spreadsheet title)."""

    source: str
    schema_: str = Field(alias="schema")
    table: str

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @property
    def label(self) -> str:
        return f"{self.source}:{self.schema_}.{self.table}"


Expr = dict[str, Any]
Grain = Literal["day", "week", "month"]
Unit = Literal["inr", "count", "metres", "days", "ratio", "percent", "inr_per_metre"]


class Measure(Body):
    agg: Literal["sum", "count", "count_distinct", "avg", "stddev", "min", "max", "last"]
    expr: Expr | None = None

    @model_validator(mode="after")
    def _check(self) -> Measure:
        if self.expr is None and self.agg != "count":
            raise ValueError(f"{self.agg} needs an expression")
        if self.expr is not None:
            check_expr(self.expr, path="measure")
        return self


class LastBy(Body):
    partition: list[str]
    order: list[str]


class AtStake(Body):
    """The ₹ that flows through what a non-₹ metric measures (dispatch time → line revenue at
    that plant): the money at stake when it goes wrong (Sweep, M6)."""

    metric: str


class Distribution(Body):
    """Per-event values of a base metric (orders by order id), for Bimodal average (M6)."""

    metric: str
    key: str  # the event's key column in that metric's table
    days: int = 365


class MetricBody(Body):
    name: str
    description: str = ""
    family: str | None = None
    variant: str | None = None
    # the area Summary groups it under (M16.1): Sales, Collections … or the owner's own name
    area: str | None = None
    status: Literal["suggested", "active", "declined", "needs_source"] = "active"
    grain: Grain = "month"
    unit: Unit = "inr"
    kind: Literal["flow", "snapshot"] = "flow"
    # a base metric: one table, one measure
    table: TableRef | None = None
    measure: Measure | None = None
    date: str | None = None  # flow: the business date column
    last_by: LastBy | None = None  # measure "last": the latest row per partition (bank balance)
    filters: list[Expr] = Field(default_factory=list)
    breakdowns: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # columns shown as evidence rows
    # a derived metric: a formula over other metrics
    formula: Expr | None = None
    breakdown_formulas: dict[str, Expr] = Field(default_factory=dict)  # per-breakdown override
    headline: bool = False
    listed: bool = True  # False for helper components (shown inside the metrics that use them)
    needs: str | None = None  # what a needs_source metric is waiting for
    confidence: Literal["high", "check", "conflict"] = "high"
    confirmed_by: str | None = None
    confirmed_at: dt.date | None = None
    at_stake: AtStake | None = None
    distribution: Distribution | None = None
    # a pure scale on a base metric's values (0.001 for metres → kilometres): applied by the
    # engine, outside the definition, so changing it converts the stored points exactly
    scale: str = "1"

    @field_validator("area", mode="before")
    @classmethod
    def _area(cls, v: Any) -> str | None:
        from othor.ontology.areas import clean

        return clean(v)

    @model_validator(mode="after")
    def _check(self) -> MetricBody:
        base = self.table is not None or self.measure is not None
        try:
            ok = Decimal(self.scale) > 0
        except InvalidOperation:
            ok = False
        if not ok:
            raise ValueError("scale is a positive decimal")
        if self.scale != "1" and not base:
            raise ValueError("only a base metric has a scale (a derived metric uses its parts)")
        if self.status == "needs_source" and self.formula is None and not base:
            return self
        if base == (self.formula is not None):
            raise ValueError("a metric is either a base metric (table + measure) or derived")
        if base:
            if self.table is None or self.measure is None:
                raise ValueError("a base metric needs a table and a measure")
            if self.kind == "flow" and not self.date:
                raise ValueError("a flow metric needs its business date column")
            if self.measure.agg == "last" and self.last_by is None:
                raise ValueError("a 'last' measure needs last_by (partition and order)")
        else:
            check_expr(self.formula, derived=True, path="formula")
            for k, f in self.breakdown_formulas.items():
                check_expr(f, derived=True, path=f"breakdown_formulas.{k}")
        for i, f in enumerate(self.filters):
            check_expr(f, path=f"filters[{i}]")
        return self


class MetricFamilyBody(Body):
    name: str
    base: str  # metric key
    variants: list[str] = Field(default_factory=list)


class Entity(Body):
    """A breakdown whose values are entities with a life of their own (customers): Missing
    records and Concentration need it (M6). Every column is checked against the personal flags."""

    table: TableRef  # where the entities live (erp_db.customers)
    metric: str  # the events that show it's alive (invoiced value, by this breakdown, per day)
    status_column: str | None = None
    active_values: list[str] = Field(default_factory=list)
    identity_column: str | None = None  # a tax id (GSTIN): "billing moved to another entity"
    created_column: str | None = None


class BreakdownBody(Body):
    name: str
    column: str
    table: str | None = None  # a joined table (by a join rule); None = the fact table
    vocabulary: str | None = None  # a prefix map that derives the value (plant from item code)
    alternatives: list[dict[str, str]] = Field(default_factory=list)  # [{table, column}] per source
    cap: int = 50
    entity: Entity | None = None


class OwnerBody(Body):
    """A person and the areas they own (a name only; members come in v1.2). Areas are metric
    keys, families, breakdown values or plain words ("Coimbatore dispatch")."""

    name: str
    role: str = ""
    areas: list[str] = Field(default_factory=list)


class VocabularyBody(Body):
    term: str
    text: str = ""
    kind: Literal["definition", "values", "prefix_map"] = "definition"
    values: list[str] = Field(default_factory=list)
    entries: dict[str, str] = Field(default_factory=dict)  # prefix → label
    other: str = "Unmapped"
    column: str | None = None  # prefix_map: the code column it reads

    @field_validator("entries")
    @classmethod
    def _prefixes(cls, v: dict[str, str]) -> dict[str, str]:
        for k in v:
            if not k or "%" in k or "_" in k or "'" in k:
                raise ValueError(f"prefix {k!r} must be plain text")
        return v


class AliasBody(Body):
    term: str
    means: str
    # a name another source uses for an entity (M6b, 6h): `source`'s term means the entity
    # `entity`'s value `means`; a join `via: alias` reads through these
    entity: str | None = None
    source: str | None = None


class CustomerBody(Body):
    name: str
    code: str | None = None
    note: str = ""


class CompetitorBody(Body):
    name: str
    note: str = ""


class Window(Body):
    kind: Literal["annual", "range", "quarter_end_days", "week_of"]
    start: str | None = None  # annual: "08-10"; range: ISO date
    end: str | None = None
    days: int | None = None
    dates: list[dt.date] = Field(default_factory=list)  # week_of: one date per year


class KnownPatternBody(Body):
    name: str
    window: Window
    metrics: list[str] = Field(default_factory=list)  # empty = all
    entity: str | None = None
    plant: str | None = None
    confirmed: bool = True
    answered_on: dt.date | None = None  # when the owner said so on Home (a resolution's reason)
    # days after the window still covered: the catch-up after a shutdown (docs/04 §4, M7)
    settle_days: int = Field(default=0, ge=0, le=60)
    short: str | None = None  # how Sweep names it in a sentence ("the Surat shutdown")


class GoalBody(Body):
    name: str
    metric: str
    target: Annotated[str, Field(pattern=r"^-?\d+(\.\d+)?$")]
    direction: Literal["at_least", "at_most"] = "at_least"
    by: dt.date
    aggregate: Literal["fiscal_year_total", "value"] = "value"
    # the owner marked it at risk on Home (M10a, a goal proposal); shown while the run's goal
    # test still finds it at risk, "on track · marked at risk <date>" after
    at_risk_since: dt.date | None = None


class BusinessFactBody(Body):
    text: str
    kind: Literal["description", "fiscal_year", "currency", "timezone", "other"] = "description"


Share = Annotated[str, Field(pattern=r"^(0(\.\d+)?|1(\.0+)?)$")]  # a share, 0 to 1


class WeatherBody(Body):
    """The state of the business's thresholds (v0.12 part 1b, docs/plans/business-weather.md):
    each one overrides the default in `othor.briefs.weather.DEFAULTS`. Compiled into the spec's
    `weather` block; no settings screen yet."""

    material_share: Share | None = None
    big_share: Share | None = None
    large_share: Share | None = None
    today_days: int | None = Field(default=None, ge=1, le=90)
    near_days: int | None = Field(default=None, ge=1, le=180)
    sudden_days: int | None = Field(default=None, ge=1, le=60)
    several: int | None = Field(default=None, ge=1, le=50)
    fog_history_days: int | None = Field(default=None, ge=0, le=730)
    fog_ran_share: Share | None = None
    active_weeks: int | None = Field(default=None, ge=1, le=52)
    active_share: Share | None = None
    fog_band_share: Share | None = None


class AnswerBody(Body):
    question: str
    answer: str


class TableUseBody(Body):
    source_id: str
    schema_: str = Field(alias="schema")
    table: str
    use: str | None
    reason: str | None = None
    without_personal: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class RuleBody(Body):
    kind: Literal[
        "book_of_record",
        "unit_conversion",
        "currency",
        "filter",
        "join",
        "reread",
        "threshold",
        "reconcile",
        "pair",
        "allocation",
    ]
    text: str = ""
    # book_of_record
    concept: str | None = None
    source: str | None = None
    over: list[str] = Field(default_factory=list)
    # unit_conversion · currency · filter · reread
    table: TableRef | None = None
    column: str | None = None
    when: Expr | None = None
    factor: str | None = None
    to_unit: str | None = None
    rate_column: str | None = None
    expr: Expr | None = None
    days: int | None = None
    # join
    to: TableRef | None = None
    on: list[tuple[str, str]] = Field(default_factory=list)  # (fact column, joined column)
    vocabulary: str | None = None  # join through a prefix map (item code → plant)
    via: Literal["alias"] | None = None  # join through alias records (a match by name, 6h)
    # threshold (M6): a limit on an amount column, and the band either side Sweep compares
    limit: str | None = None
    band: str | None = None
    label: str = ""
    # reconcile (M6): keys = `table` (left, key `on[0][0]`, `date`, value `column`, filter `when`,
    # older than `days`) against `to` (right, key `on[0][1]`); periods = two metrics in `over`
    date: str | None = None
    grain: Literal["week", "month"] | None = None
    # pair (M6): two metrics in `over` that move together, or the first ahead of the second
    relation: Literal["together", "leads"] | None = None
    # allocation (decided 4 Nov): spread `metric` over `breakdown` by the `weight` column of the
    # table `to`, linked by `on`; with `grain`, within the same period of `date` (rebates by the
    # customer's plant mix in the month). Whatever has no weight goes to "Unmapped".
    metric: str | None = None
    breakdown: str | None = None
    weight: str | None = None

    @model_validator(mode="after")
    def _check(self) -> RuleBody:
        need: dict[str, tuple[str, ...]] = {
            "book_of_record": ("concept", "source"),
            "unit_conversion": ("table", "column", "when", "factor"),
            "currency": ("table", "column", "rate_column"),
            "filter": ("table", "expr"),
            "join": ("table",),
            "reread": ("table", "days"),
            "threshold": ("table", "column", "limit", "band"),
            "reconcile": (),
            "pair": ("over", "relation"),
            "allocation": ("metric", "breakdown", "to", "on", "weight"),
        }
        for f in need[self.kind]:
            if not getattr(self, f):
                raise ValueError(f"a {self.kind} rule needs {f}")
        if self.kind == "join" and not ((self.to and self.on) or (self.vocabulary and self.column)):
            raise ValueError("a join rule needs to + on, or a vocabulary and its column")
        if self.kind == "reconcile":
            keys = self.table and self.to and self.on and self.date and self.days
            periods = len(self.over) == 2
            if not (keys or periods):
                raise ValueError(
                    "a reconcile rule needs table, to, on, date and days (keys), "
                    "or two metrics in over (periods)"
                )
        if self.kind == "pair" and len(self.over) != 2:
            raise ValueError("a pair rule names two metrics in over")
        for v in (self.factor, self.limit, self.band):
            if v is not None:
                _decimal(v)
        for e in (self.when, self.expr):
            if e is not None:
                check_expr(e, path=self.kind)
        return self


class MaterialityBody(Body):
    """The floor the owner sets (M15.6): the smallest amount, in the company's currency, worth
    telling them about. Compiled into the spec's detector floor; without it, ₹5 L (or $5,000)."""

    amount: Annotated[str, Field(pattern=r"^[1-9]\d{0,11}(\.\d{1,2})?$")]
    source: Literal["default", "owner"] = "owner"


BODIES: dict[str, type[Body]] = {
    "metric": MetricBody,
    "metric_family": MetricFamilyBody,
    "breakdown": BreakdownBody,
    "vocabulary": VocabularyBody,
    "alias": AliasBody,
    "customer": CustomerBody,
    "competitor": CompetitorBody,
    "known_pattern": KnownPatternBody,
    "goal": GoalBody,
    "business_fact": BusinessFactBody,
    "answer": AnswerBody,
    "table_use": TableUseBody,
    "rule": RuleBody,
    "owner": OwnerBody,
    "weather": WeatherBody,
    "materiality": MaterialityBody,
}
CORE_BODIES = frozenset(BODIES)  # the core's record types (a plug-in adds others)


def register_body(type_: str, model: type[Body]) -> None:
    """A plug-in's Ontology record type (M20.1); the core's types can't be replaced."""
    if type_ in BODIES and BODIES[type_] is not model:
        raise ValueError(f"record type {type_!r} is already taken")
    BODIES[type_] = model


def validate_body(type_: str, body: dict[str, Any]) -> dict[str, Any]:
    """Validate and return the body in canonical JSON form (aliases, dates as ISO strings)."""
    model = BODIES.get(type_)
    if model is None:
        raise ValueError(f"unknown record type {type_!r}")
    return model.model_validate(body).model_dump(mode="json", by_alias=True, exclude_none=True)
