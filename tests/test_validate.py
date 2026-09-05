"""Hard validation gates (spec 4.2)."""
from mlearn.validate import (anchor_in_body, anchor_word_count, count_words,
                             figures_pass, mermaid_valid, validate_card)

TOOLS = __import__("pathlib").Path(__file__).resolve().parent.parent / "tools"

GOOD_MERMAID = "flowchart TD\n  A[Loop] --> B{Hoist?}\n  B -->|yes| C[Above]\n"
BAD_MERMAID = "flowchart TD\n  A -->\n"

BODY = ("lorem ipsum dolor sit amet " * 60)  # 360 words, within the 200-500 band
LONG_BODY = "word " * 1101  # > 500: used for over-limit checks


def base_card(**over):
    card = {
        "title": "t", "hook": "h", "body_md": "word " * 350,
        "diagram_type": "concept", "diagram_src": GOOD_MERMAID,
        "figures_json": None, "anchor_quote": "the quick brown fox",
        "prompts": [
            {"question": "What is the mechanism?", "answer": "A sufficiently long answer "
             "that explains the mechanism in detail for the reader."},
            {"question": "Why does it matter?", "answer": "Another long enough answer "
             "covering the consequence in full detail."},
        ],
    }
    card.update(over)
    return card


def test_anchor_verbatim_gate():
    assert anchor_in_body("the quick brown fox", "jumps over the quick brown fox today")
    assert not anchor_in_body("quick brown", "the quick  brown fox")  # double space
    assert not anchor_in_body("", "anything")


def test_anchor_word_limit():
    assert anchor_word_count("one two three") == 3
    assert anchor_word_count("a " * 26) > 25


def test_mermaid_gate():
    ok, err = mermaid_valid(GOOD_MERMAID, TOOLS)
    assert ok, err
    ok, err = mermaid_valid(BAD_MERMAID, TOOLS)
    assert not ok
    assert "PARSE_ERROR" in err or "Parse error" in err


def test_missing_anchor_rejected():
    ok, errors = validate_card(base_card(anchor_quote="not in body"), BODY, TOOLS)
    assert not ok
    assert any("anchor_quote not found" in e for e in errors)


def test_long_anchor_rejected():
    ok, errors = validate_card(base_card(anchor_quote="word " * 27), BODY, TOOLS)
    assert not ok
    assert any("> 25 words" in e for e in errors)


def test_bad_mermaid_rejected():
    ok, errors = validate_card(base_card(diagram_src=BAD_MERMAID), BODY, TOOLS)
    assert not ok
    assert any("mermaid parse failed" in e for e in errors)


def test_word_count_bounds():
    short = base_card(body_md="short body")
    ok, errors = validate_card(short, BODY, TOOLS)
    assert not ok
    assert any("outside" in e for e in errors)
    long = base_card(body_md=LONG_BODY)
    ok, errors = validate_card(long, BODY, TOOLS)
    assert not ok
    assert any("outside" in e for e in errors)


def test_too_few_prompts_rejected():
    ok, errors = validate_card(base_card(prompts=[]), BODY, TOOLS)
    assert not ok
    assert any(">= 2 prompts" in e for e in errors)


def test_prompt_question_must_end_with_question_mark():
    card = base_card(prompts=[{
        "question": "Not a question", "answer": "This answer is long enough to pass the gate."}])
    ok, errors = validate_card(card, BODY, TOOLS)
    assert not ok
    assert any("question must end with '?'" in e for e in errors)


def test_data_diagram_requires_figures():
    card = base_card(diagram_type="data", figures_json=None)
    ok, errors = validate_card(card, BODY, TOOLS)
    assert not ok
    assert any("figures_json is empty" in e for e in errors)


def test_data_diagram_number_must_be_sourced():
    source = "the quick brown fox. the yield in year 1 was 12.5 percent in that year"
    card = base_card(
        diagram_type="data",
        diagram_src='xychart-beta\n  x-axis [1, 2]\n  y-axis "Yield"\n  bar [12.5, 99.9]',
        figures_json='[{"value": 12.5, "source": "the yield was 12.5 percent"}]',
    )
    ok, errors = validate_card(card, source, TOOLS)
    assert not ok
    assert any("99.9" in e for e in errors), errors
    assert any("'1'" in e for e in errors), errors


def test_figure_span_must_be_verbatim():
    source = "the quick brown fox. in year 1 the yield was 12.5 percent in that year"
    card = base_card(
        diagram_type="data",
        diagram_src='xychart-beta\n  x-axis [1]\n  y-axis "Yield"\n  bar [12.5]',
        figures_json=json_dumps([
            {"value": 1, "source": "year 1"},
            {"value": 12.5, "source": "yield was 12.5 percent-ish made up"},
        ]),
    )
    ok, errors = validate_card(card, source, TOOLS)
    assert not ok
    assert any("lacks a verbatim source substring" in e for e in errors), errors


def test_figures_pass_on_valid_data_diagram():
    source = ("the quick brown fox. the yield in year 1 was 12.5 percent and "
              "in year 2 it reached 20 percent the next")
    card = base_card(
        diagram_type="data",
        diagram_src='xychart-beta\n  title "Yield"\n  x-axis [1, 2]\n  y-axis "Yield"\n  bar [12.5, 20]',
        figures_json=json_dumps([
            {"value": 1, "source": "year 1"},
            {"value": 2, "source": "year 2"},
            {"value": 12.5, "source": "12.5 percent"},
            {"value": 20, "source": "20 percent"},
        ]),
    )
    ok, errors = validate_card(card, source, TOOLS)
    assert ok, errors


def test_count_words():
    assert count_words("one two three") == 3
    assert count_words("") == 0


def json_dumps(x):
    import json
    return json.dumps(x)