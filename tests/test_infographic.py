"""Visual lanes: infographic SVG gates, inline mermaid fences, projection."""

import sqlite3

from mlearn.project import render_card, write_cards
from mlearn.validate import (body_mermaid_fences, infographic_text,
                             infographic_valid, validate_card)

from conftest import seed_three

GOOD_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 520">'
            '<rect x="10" y="10" width="780" height="500" fill="#101216"/>'
            '<text x="40" y="60" fill="#eee" font-size="30">Index funds beat most pros</text>'
            '<text x="40" y="140" fill="#f6b93b" font-size="64">92%</text>'
            '<text x="40" y="200" fill="#ccc" font-size="24">of active funds underperform</text>'
            '<text x="40" y="380" fill="#ddd" font-size="20">1. Choose · 2. Buy · 3. Hold</text>'
            '<text x="40" y="460" fill="#9fdcb8" font-size="22">Takeaway: match the market, cheaply.</text>'
            '</svg>')

BAD_SCRIPT_SVG = GOOD_SVG.replace(
    '</svg>', '<script>alert(1)</script></svg>')

TOO_LONG_TEXT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 520">'
                 '<rect width="800" height="520" fill="#101216"/>'
                 '<text x="10" y="120">' + ("x" * 400) + '</text>'
                 '<text x="10" y="460">Takeaway fills the canvas.</text></svg>')

SOURCE_BODY = ("the quick brown fox jumps over the lazy dog — " * 6)


def _base(**over):
    card = {
        "title": "t", "hook": "h", "body_md": "word " * 350,
        "diagram_type": "concept", "diagram_src": "",
        "infographic_svg": None, "figures_json": None,
        "anchor_quote": "the quick brown fox jumps over the lazy dog",
        "prompts": [
            {"question": "What is the mechanism here?",
             "answer": "A sufficiently long answer that explains the mechanism "
                       "in detail for the reader."},
            {"question": "Why does it matter?",
             "answer": "Another long enough answer covering the consequence "
                       "in full detail."},
        ],
    }
    card.update(over)
    return card


def test_infographic_valid_accepts_good_svg():
    ok, err = infographic_valid(GOOD_SVG)
    assert ok, err
    assert "92%" in infographic_text(GOOD_SVG)


def test_infographic_rejects_script():
    ok, err = infographic_valid(BAD_SCRIPT_SVG)
    assert not ok
    assert "banned" in err


def test_infographic_rejects_no_text():
    ok, err = infographic_valid('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>')
    assert not ok and "text" in err


def test_infographic_rejects_missing_viewbox():
    ok, err = infographic_valid('<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="1">hi</text></svg>')
    assert not ok and "viewBox" in err


def test_infographic_rejects_long_text():
    ok, err = infographic_valid(TOO_LONG_TEXT)
    assert not ok and "too long" in err


def test_infographic_rejects_external_href():
    svg = GOOD_SVG.replace('</svg>', '<image href="https://evil.example/x.png" x="0" y="0" width="1" height="1"/></svg>')
    ok, err = infographic_valid(svg)
    assert not ok and "external" in err


def test_infographic_rejects_oversize():
    big = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 520"><text x="1" y="2">' + ("y" * 16_000) + '</text></svg>'
    ok, err = infographic_valid(big)
    assert not ok and "too large" in err


def test_body_mermaid_fences_extracts_blocks():
    md = "some text\n```mermaid\ngraph LR\n  A-->B\n```\nmore\n```mermaid\nflowchart TD\n  X\n```"
    fences = body_mermaid_fences(md)
    assert len(fences) == 2
    assert "A-->B" in fences[0]
    assert body_mermaid_fences("no fences here") == []


ANTV_BANNER = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 688 145">'
               '<g transform="translate(0, 60)">'
               '<foreignObject width="200" height="30" y="0" x="0">'
               '<span>92% of funds underperform the market</span>'
               '</foreignObject></g></svg>')


def test_truncation_detector():
    """list-pyramid-* caps at 6 items by design; the detector must fail the
    attempt so the retry loop switches to a scaling template."""
    import shutil
    from pathlib import Path
    if shutil.which("node") is None:
        import pytest
        pytest.skip("node not available")
    tools = Path(__file__).resolve().parents[1] / "tools"
    from mlearn.infographic import render_spec
    items7 = "".join(f"    - label L{i}\n      desc layer {i}\n" for i in range(1, 8))
    svg, err = render_spec(
        f"infographic list-pyramid-badge-card\ndata\n  title Seven\n  lists\n{items7}", tools)
    assert svg is None and "dropped items" in err and "L7" in err
    svg, err = render_spec(
        f"infographic list-column-done-list\ndata\n  title Seven\n  lists\n{items7}", tools)
    assert svg is not None and err == ""


def test_antv_banner_needs_non_strict_layout(tmp_path):
    """Engine-rendered banners are tight by construction: the fill-height
    layout gate only applies to the raw hand-written lane."""
    from mlearn.validate import infographic_valid
    ok, err = infographic_valid(ANTV_BANNER)          # strict (raw lane)
    assert not ok and "fill the canvas" in err
    ok, err = infographic_valid(ANTV_BANNER, strict_layout=False)
    assert ok, err


def test_foreignobject_event_handler_rejected():
    from mlearn.validate import infographic_valid
    evil = GOOD_SVG.replace(
        '<text x="40" y="60"', '<foreignObject onmouseover="evil()" width="10" height="10">x</foreignObject><text x="40" y="60"')
    ok, err = infographic_valid(evil)
    assert not ok and "event handlers" in err


def test_foreignobject_text_accepted(tmp_path):
    """AntV renders text as foreignObject HTML spans — must pass the gates
    (script/attr bans still apply)."""
    d = _base()
    d["diagram_src"] = ""
    d["infographic_svg"] = ANTV_BANNER
    ok, errs = validate_card(d, SOURCE_BODY, tmp_path / "tools",
                             infographic_strict=False)
    assert ok, errs


def test_apply_infographic_lane_renders_spec(tmp_path):
    import shutil
    from pathlib import Path
    if shutil.which("node") is None:
        import pytest
        pytest.skip("node not available")
    from mlearn.generate import apply_infographic_lane
    tools = Path(__file__).resolve().parents[1] / "tools"
    spec = ("infographic list-grid-simple\ndata\n  title Stats\n  lists\n"
            "    - label 92%\n      desc of funds underperforming the index\n")
    card = {"diagram_src": "", "infographic_spec": spec}
    errs = apply_infographic_lane(card, tools)
    assert errs == [] and card.get("_infographic_lane") == "antv"
    assert card["infographic_svg"].startswith("<svg")
    assert "#262626" not in card["infographic_svg"], "light-theme text leaked"
    assert "#fff" in card["infographic_svg"], "dark theme text missing"
    from mlearn.validate import infographic_valid
    ok, err = infographic_valid(card["infographic_svg"], strict_layout=False)
    assert ok, err


def test_apply_infographic_lane_rejects_bad_spec():
    from mlearn.generate import apply_infographic_lane
    card = {"infographic_spec": "this is not a spec"}
    errs = apply_infographic_lane(card, "tools")
    assert errs and "spec invalid" in errs[0]
    assert "infographic_svg" not in card


def test_apply_infographic_lane_no_spec():
    from mlearn.generate import apply_infographic_lane
    card = {"diagram_src": "flowchart TD\nA-->B"}
    assert apply_infographic_lane(card, "tools") == []
    assert "infographic_svg" not in card


def test_short_content_rejected(tmp_path):
    """Fill-height gate: content that stops above ~78% of the canvas fails."""
    d = _base()
    d["diagram_src"] = ""
    d["infographic_svg"] = GOOD_SVG
    svg = d["infographic_svg"].replace('y="380"', 'y="300"').replace('y="460"', 'y="300"')
    d["infographic_svg"] = svg
    ok, errs = validate_card(d, SOURCE_BODY, tmp_path / "tools")
    assert not ok, errs
    assert any("must fill the canvas" in e for e in errs)


def test_num_none_is_zero():
    from mlearn.validate import _num
    assert _num(None) == 0.0
    assert _num("42") == 42.0


def test_no_visual_is_rejected(tmp_path):
    d = _base()
    d["diagram_src"] = ""
    d["infographic_svg"] = None
    ok, errs = validate_card(d, SOURCE_BODY, tmp_path / "tools")
    assert not ok
    assert any("at least one visual" in e for e in errs)


def test_infographic_lane_passes_gates(tmp_path):
    d = _base()
    d["diagram_src"] = ""
    d["infographic_svg"] = GOOD_SVG
    ok, errs = validate_card(d, SOURCE_BODY, tmp_path / "tools")
    assert ok, errs


def test_inline_fence_line_cap(tmp_path):
    d = _base()
    d["diagram_src"] = ""
    d["body_md"] = "word " * 340 + "\n```mermaid\ngraph LR\n" + "\n".join(
        f"  A{i}-->B{i}" for i in range(12)) + "\n```"
    ok, errs = validate_card(d, SOURCE_BODY, tmp_path / "tools")
    assert not ok
    assert any("fence 1 too busy" in e for e in errs)


def test_write_cards_embeds_infographic(tmp_path, db):
    ids = seed_three(db)
    db.execute("UPDATE cards SET infographic_svg = ? WHERE id = ?", (GOOD_SVG, ids[0]))
    db.commit()
    paths = write_cards(db, tmp_path / "cards")
    md = paths[0].read_text()
    assert "![[2026" in md and "_infographic.svg]]" in md
    svgs = list((tmp_path / "cards").rglob("*.svg"))
    assert len(svgs) == 1
    assert svgs[0].read_text() == GOOD_SVG


def test_write_cards_prunes_stale_svg(tmp_path, db):
    seed_three(db)
    stale = tmp_path / "cards" / "finance" / "stale_infographic.svg"
    stale.parent.mkdir(parents=True)
    stale.write_text("<svg/>")
    write_cards(db, tmp_path / "cards")
    assert not stale.exists()