"""Hermes tool gateway (spec 9.3): thin wrappers over the mlearn CLI.

Since these are plain CLI calls (--json), the same wrappers work for any MCP
consumer without modification. Wire them into the Hermes tool registry or an
MCP server by importing/executing these functions.

Every function returns the parsed JSON dict from the CLI.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
BIN = REPO / ".venv" / "bin" / "mlearn"
DEFAULT_TIMEOUT = 120

# The generate pipeline needs a provider key; serving/grading do not.
KEY_ENVS = ("API_SERVER_KEY", "OPENROUTER_API_KEY")


def _run(*args: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    env = dict(os.environ)
    cmd = [str(BIN), *args, "--json"]
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"mlearn {' '.join(args)} timed out after {timeout}s")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(" ".join(cmd) + " failed: " + (tail[-1] if tail else "?"))
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"mlearn returned non-JSON: {r.stdout[:200]!r} ({e})")


def mlearn_next(count: int = 1) -> dict:
    """Serve interleaved cards + due prompts (never generates on the path)."""
    return _run("next", "--count", str(count))


def mlearn_grade(prompt_id: int, grade: int) -> dict:
    """The only external write path: 1=again 2=hard 3=good 4=easy."""
    return _run("grade", str(prompt_id), str(grade))


def mlearn_signal(card_id: int, kind: str) -> dict:
    """more_like_this | less_like_this | skip | opened_source."""
    return _run("signal", str(card_id), kind)


def mlearn_search(query: str, limit: int = 10) -> dict:
    """Semantic search over cards."""
    return _run("search", query, "--limit", str(limit))


def mlearn_stats() -> dict:
    """Buffer depth, cluster posteriors, grade distribution."""
    return _run("stats")


def mlearn_tick() -> dict:
    """Cron entry: refill buffer below floor + weekly decay."""
    return _run("tick")


if __name__ == "__main__":
    import sys
    fn = sys.argv[1] if len(sys.argv) > 1 else "stats"
    out = globals()[f"mlearn_{fn}"](*sys.argv[2:])
    print(json.dumps(out, indent=2, default=str))