"""B&W (e-ink) variant: derive, id namespacing, mono gate, backfill."""
import re

from mlearn import bw
from mlearn import db as db_mod

COLOR_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 140">'
    '<defs><linearGradient id="g1">'
    '<stop offset="0%" stop-color="#FF356A" stop-opacity="0.36"/>'
    '<stop offset="100%" stop-color="#FF356A" stop-opacity="0"/>'
    "</linearGradient></defs>"
    '<rect width="400" height="140" fill="#1F1F1F"/>'
    '<text x="10" y="60" fill="#ffffff">Hello memory</text>'
    '<path d="M0 0h10" fill="url(#g1)"/>'
    '<circle r="4" fill="rgba(255,255,255,0.5)"/>'
    "</svg>"
)


def test_derive_recolors_and_keeps_alpha():
    out = bw.derive_bw(COLOR_SVG)
    assert "ff356a" not in out.lower()
    assert 'stop-opacity="0.684"' in out          # 0.36 * 1.9 boosted
    assert "rgba(200,200,200,0.5)" in out         # shape fill -> light surface, alpha kept
    assert "#f8f8f8" in out                       # dark panel bg -> near-white surface
    assert "Hello memory" in out                  # content untouched


def test_derive_ink_roles():
    """v2: text/strokes/icons go dark; box fills go light."""
    out = bw.derive_bw(COLOR_SVG)
    assert 'fill="#000000"' in out                # white text -> black ink
    assert "#c8c8c8" in out                       # gradient fill ref -> light surface tone


def test_derive_flattens_gradient_refs():
    out = bw.derive_bw(COLOR_SVG)
    assert "url(#g1)" not in out


def test_derive_role_bands():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<path d="M0 0h50v50H0z" fill="#22c55e"/>'                 # box -> light
           '<use fill="#1f1f1f" href="#i1" width="10" height="10"/>'   # icon -> dark
           '<span style="color:#ffffff">x</span>'                      # text -> black
           '<path d="M0 90h100" stroke="#D9D9D9"/>'                    # stroke -> dark
           '</svg>')
    out = bw.derive_bw(svg)
    assert 'fill="#d5d5d5"' in out                # 22c55e (max 197) -> surface tone
    assert 'fill="#222222"' in out                # 1f1f1f icon ink stays dark
    assert "color:#000000" in out                 # white text -> black
    assert 'stroke="#2a2a2a"' in out              # pale stroke -> dark


def test_derive_handles_single_quoted_attrs():
    single = COLOR_SVG.replace('"', "'")
    out = bw.derive_bw(single)
    assert "ff356a" not in out.lower()
    assert "stop-opacity='0.684'" in out
    assert "url(#g1)" not in out


def test_namespace_rewrites_refs_and_is_idempotent():
    ns, prefix = bw.namespace_ids(COLOR_SVG, "c")
    assert prefix and prefix.startswith("ml") and prefix.endswith("c-")
    ids = set(re.findall(r'id="([^"]+)"', ns))
    assert ids and all(i.startswith(prefix) for i in ids)
    refs = set(re.findall(r"url\(#([^)]+)\)", ns)) | set(re.findall(r'href="#([^"]+)"', ns))
    assert refs <= ids                            # no dangling references
    assert "data-mlearn-ns" in ns
    again, prefix2 = bw.namespace_ids(ns, "c")
    assert prefix2 is None and again == ns        # idempotent


def test_namespace_single_quoted_attrs():
    single = COLOR_SVG.replace('"', "'")
    ns, prefix = bw.namespace_ids(single, "c")
    assert prefix
    assert f"id='{prefix}g1'" in ns
    assert f"url(#{prefix}g1)" in ns


def test_namespace_keeps_original_when_refs_would_dangle():
    broken = COLOR_SVG.replace("<circle", '<use href="#missing"/><circle', 1)
    out, prefix = bw.namespace_ids(broken, "c")
    assert prefix is None and out == broken


def test_gate_rejects_saturated_and_accepts_derived():
    ok, _ = bw.qa_mono_svg(bw.derive_bw(COLOR_SVG))
    assert ok
    ok2, err = bw.qa_mono_svg(COLOR_SVG)          # raw color svg must fail
    assert not ok2 and "saturated" in err


def test_prepare_variants_end_to_end():
    color, mono, warn = bw.prepare_variants(COLOR_SVG)
    assert warn is None and color and mono
    assert "data-mlearn-ns" in color
    ok, _ = bw.qa_mono_svg(mono)
    assert ok
    # both variants coexist without id collisions (distinct prefixes)
    cids = set(re.findall(r'id="(ml[^"]*?)-', color))
    mids = set(re.findall(r'id="(ml[^"]*?)-', mono))
    assert cids and mids and cids.isdisjoint(mids)
    assert bw.prepare_variants(None) == (None, None, None)


def test_backfill_updates_db_and_is_idempotent(db):
    cid = db_mod.insert_card(
        db, item_id=None, cluster_label="technology", title="t", hook="h",
        body_md="body text", diagram_type="concept", diagram_src="",
        infographic_svg=COLOR_SVG, figures_json="[]",
        source_url="https://example.com/x", anchor_quote="q",
        prompts=[{"question": "q?", "answer": "a"}],
    )
    stats = bw.backfill_bw(db)
    assert stats["updated"] == 1 and stats["bw_failed"] == 0
    row = db.execute(
        "SELECT infographic_svg, infographic_svg_bw FROM cards WHERE id = ?",
        (cid,)).fetchone()
    assert "data-mlearn-ns" in row["infographic_svg"]
    ok, _ = bw.qa_mono_svg(row["infographic_svg_bw"])
    assert ok
    stats2 = bw.backfill_bw(db)
    assert stats2["skipped"] == 1 and stats2["updated"] == 0


def test_improve_apply_rederives_mono(db):
    """A banner update (improve) must refresh mono + namespacing, never stale."""
    from mlearn.improve import _apply
    cid = db_mod.insert_card(
        db, item_id=None, cluster_label="technology", title="t", hook="h",
        body_md="body text", diagram_type="concept", diagram_src="",
        infographic_svg=COLOR_SVG, figures_json="[]",
        source_url="https://example.com/x", anchor_quote="q",
        prompts=[{"question": "q?", "answer": "a"}],
    )
    row = db.execute("SELECT * FROM cards WHERE id = ?", (cid,)).fetchone()
    new_svg = COLOR_SVG.replace("#FF356A", "#22c55e").replace("#1F1F1F", "#334155")
    _apply(db, cid, row, {"infographic_svg": new_svg,
                          "infographic_spec": "infographic list-grid-badge-card"})
    r2 = db.execute(
        "SELECT infographic_svg, infographic_svg_bw, infographic_spec "
        "FROM cards WHERE id = ?", (cid,)).fetchone()
    assert "data-mlearn-ns" in r2["infographic_svg"]
    assert "ff356a" not in r2["infographic_svg"].lower()  # new art stored
    assert r2["infographic_svg_bw"] and "data-mlearn-ns" in r2["infographic_svg_bw"]
    ok, _ = bw.qa_mono_svg(r2["infographic_svg_bw"])
    assert ok
    assert r2["infographic_spec"] == "infographic list-grid-badge-card"
