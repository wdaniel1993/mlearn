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
            '</svg>')

BAD_SCRIPT_SVG = GOOD_SVG.replace(
    '</svg>', '<script>alert(1)</script></svg>')

TOO_LONG_TEXT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 520">'
                 '<text x="10" y="20">' + ("x" * 400) + '</text></svg>')

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