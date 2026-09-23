#!/usr/bin/env python3
"""Offline smoke for maker-pick. No TypeSafe network call."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as config_mod
from src.cli import main
from src.maker_pick import (
    FORBIDDEN_REVIEW,
    OUTPUT_FIELDS,
    maker_pick,
    resolve_criteria,
)
from src.router import route_task


class FakeChoice:
    def __init__(self, choice: str, confidence: float, probabilities: dict[str, float]) -> None:
        self.choice = choice
        self.confidence = confidence
        self.probabilities = probabilities


class FakeResult:
    def __init__(self, choices: dict[str, FakeChoice]) -> None:
        self.choices = choices


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {message}")


def _cfg(enabled: bool = True) -> dict:
    return {
        "enabled": enabled,
        "mode": "shadow",
        "model": "jev-latest",
        "thresholds": {"min_choice_confidence": 0.55},
        "logging": {"path": str(ROOT / "logs" / "runs.jsonl")},
        "maker_pick": {
            "review": {
                "codex": "Codex-style review.",
                "qa": "QA review.",
                "pooh": "must be stripped",
                "orchestrator": "must be stripped",
            }
        },
    }


def _answered() -> FakeResult:
    return FakeResult(
        {
            "maker": FakeChoice("cursor", 0.91, {"cursor": 0.94, "codex": 0.06}),
            "kick_mode": FakeChoice("interactive", 0.88, {"interactive": 0.9}),
            "implement_model": FakeChoice("cloud_default", 0.8, {"cloud_default": 0.85}),
            "effort": FakeChoice("high", 0.2, {"high": 0.4, "medium": 0.35}),
            "review": FakeChoice("qa", 0.77, {"qa": 0.8, "codex": 0.2}),
            "design": FakeChoice("skip", 0.83, {"skip": 0.86}),
            "plan": FakeChoice("codex", 0.7, {"codex": 0.74, "skip": 0.2}),
        }
    )


def main_smoke() -> int:
    cfg = _cfg()
    criteria = resolve_criteria(cfg)
    _check(FORBIDDEN_REVIEW.isdisjoint(criteria["review"]), "review still lists pooh or orchestrator")
    _check("codex" in criteria["review"] and "qa" in criteria["review"], "review lost real roles")

    calls = {"n": 0}

    def ask(state, questions, model):
        calls["n"] += 1
        _check(set(questions) == {
            "maker",
            "kick_mode",
            "implement_model",
            "effort",
            "review",
            "design",
            "plan",
        }, "question set drifted")
        review_criteria = getattr(questions["review"], "criteria", {}) or {}
        _check("pooh" not in review_criteria, "Choice criteria include pooh")
        _check("orchestrator" not in review_criteria, "Choice criteria include orchestrator")
        return _answered()

    out = maker_pick({"goal": "Add roster fields", "kind": "coding"}, ask=ask, cfg=cfg)
    for field in OUTPUT_FIELDS:
        _check(field in out, f"missing {field}")
    _check(out["choice"] == "cursor", "choice")
    _check(out["kick_mode"] == "interactive", "kick_mode")
    _check(out["model"] == "cloud_default", "model")
    _check(out["effort"] == "high", "low confidence must still honor choice")
    _check(out["review"] == "qa", "review")
    _check(out["design"] == "skip", "design")
    _check(out["plan"] == "codex", "plan")
    _check(out["below_threshold"] == ["effort"], f"below_threshold {out['below_threshold']}")
    _check(out["fail_open"] is False and out["jev_used"] is True, "expected a live mapping")
    _check(out["policy"]["no_llm_reargue"] is True, "policy")
    _check(calls["n"] == 1, "Jev should be asked once")

    def boom(state, questions, model):
        raise RuntimeError("secret request body should not leak")

    failed = maker_pick({"goal": "boom"}, ask=boom, cfg=cfg)
    blob = json.dumps(failed)
    _check(failed["fail_open"] is True and failed["jev_used"] is False, "error fail-open")
    _check(all(failed[field] for field in OUTPUT_FIELDS), "fail-open missing fields")
    _check("secret request body" not in blob, "raw exception leaked into JSON")

    skipped = maker_pick({"goal": "bypass jev please"}, ask=boom, cfg=cfg)
    _check(skipped["fail_open"] is True and skipped["reason"] == "lab disabled or bypass jev", "bypass")

    disabled = maker_pick({"goal": "off"}, ask=boom, cfg=_cfg(enabled=False))
    _check(disabled["fail_open"] is True and disabled["choice"] == "cursor", "disabled default")

    guarded = FakeResult(
        {
            "maker": FakeChoice("cursor", 0.9, {}),
            "kick_mode": FakeChoice("interactive", 0.9, {}),
            "implement_model": FakeChoice("cloud_default", 0.9, {}),
            "effort": FakeChoice("low", 0.9, {}),
            "review": FakeChoice("pooh", 0.99, {}),
            "design": FakeChoice("skip", 0.9, {}),
            "plan": FakeChoice("skip", 0.9, {}),
        }
    )
    held = maker_pick({"goal": "guard"}, ask=lambda s, q, m: guarded, cfg=cfg)
    _check(held["review"] != "pooh" and held["review"] in criteria["review"], "pooh review blocked")
    _check(held["confidence"]["review"] == 0.0, "rejected review confidence cleared")
    _check("review" in held["below_threshold"], "rejected review flagged")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.yaml"
        path.write_text(
            "enabled: false\nmode: shadow\nlogging:\n  path: logs/runs.jsonl\n",
            encoding="utf-8",
        )
        previous = config_mod.CONFIG_PATH
        config_mod.CONFIG_PATH = path
        stdout = io.StringIO()
        try:
            routed = route_task({"goal": "What is 2+2?", "kind": "chat"})
            _check(routed["action"] == "proceed_full" and routed["jev_used"] is False, "route fail-open")
            with contextlib.redirect_stdout(stdout):
                code = main(["maker-pick", '{"goal":"cli smoke","kind":"coding"}'])
                legacy = main(['{"goal":"legacy route","kind":"chat"}'])
                usage = main([])
            _check(code == 0, "cli maker-pick exit")
            decoder = json.JSONDecoder()
            roster, offset = decoder.raw_decode(stdout.getvalue().lstrip())
            _check(all(field in roster for field in OUTPUT_FIELDS), "cli JSON fields")
            _check(roster["fail_open"] is True, "cli used disabled config")
            rest = stdout.getvalue()[offset:].lstrip()
            legacy_out, _legacy_offset = decoder.raw_decode(rest)
            _check(legacy_out.get("action") == "proceed_full", "legacy route shape")
            _check(legacy == 0, "legacy route argv")
            _check(usage == 2, "usage exit")
        finally:
            config_mod.CONFIG_PATH = previous

    print("maker-pick smoke ok")
    print(json.dumps({field: out[field] for field in OUTPUT_FIELDS}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_smoke())
