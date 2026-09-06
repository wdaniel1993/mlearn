"""Configuration loading. All knobs in one place (spec 11)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "buffer_target": 20,
    "buffer_floor": 8,
    "batch_size": 12,
    "daily_cap": 5,
    "discovery_ratio": 0.7,
    "wildcard_rate": 0.15,
    "exploration_floor": 0.03,
    "dedupe_threshold": 0.92,
    "profile_lambda": 0.1,
    "decay_factor": 0.95,
    "probation_cards": 20,
    "max_probation_sources": 3,
    "retention_target": 0.9,
    "paths": {
        "data_dir": "data",
        "raw_dir": "data/raw",
        "cards_dir": "cards",
        "db": "data/app.db",
        "sources": "sources.yaml",
        "config": "config.yaml",
    },
    "generate": {
        "provider": "openrouter",
        "model": "anthropic/claude-3.5-haiku",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "temperature": 0.4,
        "max_retries": 3,
    },
    "embed": {"provider": "local", "model": "BAAI/bge-small-en-v1.5"},
    "api": {"host": "127.0.0.1", "port": 8311},
    # Topic catalog = the initial clusters (seed topics). Each entry carries
    # the LLM guardrail for that topic. Operators can add, remove, or rename
    # topics here; generation, seeding, and round-robin allocation follow
    # this list (plus any clusters born later via the wildcard arm).
    "topics": [
        {
            "name": "self_improvement",
            "guardrail": (
                "Topic self_improvement — require a study, dataset, or primary account in the article. "
                "Reject anything whose substance is exhortation (mere encouragement)."
            ),
        },
        {
            "name": "mental_health",
            "guardrail": (
                "Topic mental_health — MECHANISM AND RESEARCH ONLY: explain mechanisms and evidence "
                "(how sleep debt affects affect regulation, what a study found, how it was designed). "
                "NEVER prescriptive (no 'do X to fix your anxiety'), never framed as addressing the "
                "reader's own condition, never diagnostic. If the article can only be rendered as "
                "advice, refuse it."
            ),
        },
        {
            "name": "innovation",
            "guardrail": "Topic innovation — explain the mechanism behind the innovation or idea.",
        },
        {
            "name": "technology",
            "guardrail": "Topic technology — explain the mechanism of the technology: how it works, why.",
        },
        {
            "name": "finance",
            "guardrail": (
                "Topic finance — how instruments, markets, and mechanisms work. NEVER buy/sell/allocate "
                "guidance, never specific-security recommendations, no performance projections."
            ),
        },
        {
            "name": "psychology",
            "guardrail": (
                "Topic psychology — how the mind works: mechanisms, biases, effects, and their "
                "evidence. NEVER diagnostic, NEVER prescriptive: no therapy guidance, no coping "
                "advice, no 'this means you' claims. Describe the phenomenon and the studies."
            ),
        },
    ],
}


def find_config(start: Path | None = None) -> Path | None:
    """Walk up from start (default cwd) looking for config.yaml."""
    cur = (start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        cand = d / "config.yaml"
        if cand.is_file():
            return cand
    return None


def deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(config_path: str | Path | None = None) -> dict:
    cfg = deep_merge(DEFAULTS, {})
    path = Path(config_path) if config_path else find_config()
    if path and path.is_file():
        with open(path) as f:
            loaded = yaml.safe_load(f) or {}
        cfg = deep_merge(cfg, loaded)
    cfg["_config_path"] = str(path) if path else None
    cfg["_base_dir"] = str(path.parent.resolve()) if path else str(Path.cwd().resolve())
    return cfg


def resolve_paths(cfg: dict) -> dict:
    """Make all paths absolute, relative to the config file's directory."""
    base = Path(cfg["_base_dir"])
    out = dict(cfg)
    paths = dict(cfg["paths"])
    for key, val in paths.items():
        if val and not Path(val).is_absolute():
            paths[key] = str(base / val)
    out["paths"] = paths
    return out