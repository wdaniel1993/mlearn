"""Visual QA gates: banner self-explanation + mermaid semantic/render checks."""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mlearn import visualqa as vqa  # noqa: E402

TOOLS = Path(__file__).resolve().parents[1] / "tools"


GOOD_BANNER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 521 220" width="521" height="220">
<rect width="521" height="220" fill="#1F1F1F"/>
<text x="20" y="30" fill="#ffffff" font-size="18">Plan vs reality for this project</text>
<text x="20" y="60" fill="#22d3ee" font-size="16">Best case 27 days</text>
<text x="20" y="80" fill="#22c55e" font-size="16">Actual average 55 days</text>
</svg>"""

BARE_NUMBERS_BANNER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 521 220" width="521" height="220">
<rect width="521" height="220" fill="#1F1F1F"/>
<text x="20" y="30" fill="#22d3ee" font-size="16">27.4</text>
<text x="20" y="60" fill="#22c55e" font-size="16">33.9</text>
<text x="20" y="90" fill="#f59e0b" font-size="16">48.6</text>
<text x="140" y="30" fill="#f97316" font-size="16">55.5</text>
</svg>"""


def test_banner_with_labels_passes():
    ok, err = vqa.qa_banner_svg(GOOD_BANNER)
    assert ok, err


def test_banner_bare_numbers_rejected():
    ok, err = vqa.qa_banner_svg(BARE_NUMBERS_BANNER)
    assert not ok
    assert "self-explanatory" in err


def test_banner_garbage_rejected():
    assert not vqa.qa_banner_svg("")[0]
    assert not vqa.qa_banner_svg("<svg/>")[0]


FLOW = """flowchart LR
    A[Past delays fade] -->|feels smooth| B[Short estimate]
    B --> C[Real delays hit]
"""

STATE_LOOP = """stateDiagram-v2
    [*] --> Future task
    Future task --> Smooth story
"""


def test_mermaid_flowchart_passes():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    ok, err = vqa.qa_mermaid(FLOW, TOOLS)
    assert ok, err


def test_mermaid_state_diagram_banned():
    ok, err = vqa.qa_mermaid(STATE_LOOP, TOOLS)
    assert not ok
    assert "stateDiagram" in err


def test_mermaid_garbage_rejected():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    ok, err = vqa.qa_mermaid("this is not a diagram at all", TOOLS)
    assert not ok