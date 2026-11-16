# SPDX-License-Identifier: Apache-2.0
"""Content hashes (M10b, decided 6 Nov 2026): a version's records hash leaves out its number,
time, author, reason and citations; a spec's compiled hash leaves out its version number.
Restore gives a new number with the same content; Apply then Undo gives the original content;
an Apply that changes neither records nor citations is refused; a citation-only change is a
"Source updated" version that reuses the current spec."""

from __future__ import annotations

import sqlalchemy as sa

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.ontology import content, service
from othor.ontology.compiler import compile_version
from othor.ontology.models import OntologyVersion

A = {"term": "Revenue", "text": "net of GST"}


def _v(s, n):
    return s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == n)).one()


def _vocab(s):
    return next(r for r in service.active(s, ["vocabulary"]) if r.source_ref)


def test_the_records_hash_ignores_order_citations_and_who():
    rows = [("vocabulary", "a", A), ("goal", "g", {"name": "x"})]
    assert content.records_hash(rows) == content.records_hash(list(reversed(rows)))
    assert content.records_hash(rows) != content.records_hash(
        [("vocabulary", "a", {**A, "text": "gross"}), ("goal", "g", {"name": "x"})]
    )
    doc = {"ontology_version": 3, "metrics": {"m": {"queries": {"month": "select 1"}}}}
    assert content.compiled_hash(doc) == content.compiled_hash({**doc, "ontology_version": 9})
    assert content.compiled_hash(doc) != content.compiled_hash({**doc, "metrics": {}})


def test_restore_gives_a_new_number_with_the_same_content(world):
    with world.sf() as s, tenant_scope(world.cid):
        company = world.company(s)
        v1 = _v(s, 1)
        spec1 = compile_version(s, company, 1)
        assert v1.content_hash and spec1.content_hash
        voc = _vocab(s)
        v2 = service.apply(
            s,
            [service.put("vocabulary", voc.key, {**voc.body, "text": "changed"})],
            actor="o",
            reason="edit",
        )
        assert v2.content_hash != v1.content_hash
        v3 = service.restore(s, 1, actor="o")
        assert v3.number == 3 and v3.content_hash == v1.content_hash
        spec3 = compile_version(s, company, 3)
        assert spec3.hash != spec1.hash  # a version's identity includes its number
        assert spec3.content_hash == spec1.content_hash
        assert {m: e.get("definition_hash") for m, e in spec3.compiled["metrics"].items()} == {
            m: e.get("definition_hash") for m, e in spec1.compiled["metrics"].items()
        }
        assert s.scalars(sa.select(Event).where(Event.action == "ontology.restored")).first()


def test_apply_then_undo_gives_the_original_content(world):
    with world.sf() as s, tenant_scope(world.cid):
        before = service.history(s)[0].content_hash
        voc = _vocab(s)
        v = service.apply(
            s,
            [service.put("vocabulary", voc.key, {**voc.body, "text": "changed"})],
            actor="o",
            reason="edit",
        )
        u = service.undo(s, v.number, actor="o")
        assert u.content_hash == before


def test_an_apply_that_changes_nothing_is_refused(world):
    with world.sf() as s, tenant_scope(world.cid):
        n = service.current_number(s)
        voc = _vocab(s)
        assert (
            service.apply(
                s, [service.put("vocabulary", voc.key, voc.body)], actor="o", reason="same"
            )
            is None
        )
        assert service.restore(s, n, actor="o") is None  # the current content
        assert service.current_number(s) == n


def test_a_citation_only_change_is_a_source_updated_version_on_the_same_spec(world):
    with world.sf() as s, tenant_scope(world.cid):
        company = world.company(s)
        spec_before = compile_version(s, company)
        voc = _vocab(s)
        before = service.history(s)[0]
        v = service.apply(
            s,
            [service.put("vocabulary", voc.key, voc.body, {"upload": "MIS deck · slide 4"})],
            actor="o",
            reason="Better source",
        )
        assert v is not None and v.reason == "Source updated"
        assert v.content_hash == before.content_hash
        assert v.diff["changes"][0]["op"] == "cite"
        assert _vocab(s).source_ref == {"upload": "MIS deck · slide 4"}
        assert compile_version(s, company).id == spec_before.id  # nothing recompiled
        assert content.same_as(s, v) == before.number
        # the same citation again changes nothing
        assert (
            service.apply(
                s,
                [service.put("vocabulary", voc.key, voc.body, {"upload": "MIS deck · slide 4"})],
                actor="o",
                reason="again",
            )
            is None
        )
        # Undo puts the old citation back
        u = service.undo(s, v.number, actor="o")
        assert u is not None and _vocab(s).source_ref == voc.source_ref


def test_the_migration_backfills_each_versions_hash(world):
    """0029 fills content_hash for versions written before it with the same value the service
    computes (here: clear it, run the backfill again by downgrading and upgrading)."""
    from othor.migrations.runner import downgrade, upgrade

    url = str(world.sf.kw["bind"].url.render_as_string(hide_password=False))
    with world.sf() as s, tenant_scope(world.cid):
        voc = _vocab(s)
        service.apply(
            s,
            [service.put("vocabulary", voc.key, {**voc.body, "text": "changed"})],
            actor="o",
            reason="edit",
        )
        s.commit()
        want = {v.number: v.content_hash for v in service.history(s)}
    world.sf.kw["bind"].dispose()
    downgrade(url, "0028_home")
    upgrade(url, "0029_content_hash")
    with world.sf() as s, tenant_scope(world.cid):
        assert {v.number: v.content_hash for v in service.history(s)} == want
