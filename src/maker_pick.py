"""Kick-roster Choice questions for one-pager consumers.

One ``system_one`` call answers maker, kick mode, implement model, effort,
review assignee, and whether design/plan are skipped or assigned. Callers
honor ``choice`` directly. This module does not ask another model to re-argue.
"""

from __future__ import annotations

from typing import Any, Callable

from src.config import load_config, resolve_log_path
from src.logger import log_run
from src.router import _bypassed

# JSON keys one-pagers read. ``choice`` is the implement maker.
OUTPUT_FIELDS = (
    "choice",
    "kick_mode",
    "model",
    "effort",
    "review",
    "design",
    "plan",
)

# Question id → JSON field. Question ids are not sent to Jev.
QUESTION_TO_FIELD = {
    "maker": "choice",
    "kick_mode": "kick_mode",
    "implement_model": "model",
    "effort": "effort",
    "review": "review",
    "design": "design",
    "plan": "plan",
}

# Review is a checker, never the orchestrator.
FORBIDDEN_REVIEW = frozenset({"pooh", "orchestrator"})

DEFAULT_MAKERS = {
    "cursor": "Cursor cloud or IDE agent edits the repo and opens the PR.",
    "codex": "Codex-style coding agent implements from the kick brief.",
    "claude": "Claude Code implements from the kick brief.",
    "grok": "Grok Bot implements inside its own session.",
}

DEFAULT_KICK_MODES = {
    "interactive": "A person or parent stays in the loop during the kick.",
    "background": "Run the kick unattended and report when it finishes.",
    "skip": "Do not start an implement kick.",
}

# ``cloud_default`` or an explicit model id the kick platform accepts.
# Replace ``models`` in config to match the platform's real id list.
DEFAULT_MODELS = {
    "cloud_default": "Use the cloud agent's configured default model.",
    "grok-4.7-high": "Explicit model id grok-4.7-high.",
    "gpt-5.6-sol-medium": "Explicit model id gpt-5.6-sol-medium.",
    "claude-sonnet-5-thinking-medium": "Explicit model id claude-sonnet-5-thinking-medium.",
}

DEFAULT_EFFORTS = {
    "low": "Small local change with little exploration.",
    "medium": "A few files and ordinary tests.",
    "high": "Cross-cutting change with tests and docs.",
    "xhigh": "Wide or risky change that needs a deep pass over the tree.",
}

DEFAULT_REVIEW = {
    "codex": "Codex-style self-check of the diff. Not the orchestrator.",
    "qa": "QA pass for behavior, regressions, and acceptance. Not the orchestrator.",
    "security": "Security pass over auth, data, and unsafe changes. Not the orchestrator.",
    "skip": "No separate review assignee for this kick.",
}

# Used only when Jev is disabled, bypassed, or unavailable.
# These are complete answers, not a missing-field placeholder.
FAIL_OPEN_DEFAULTS = {
    "choice": "cursor",
    "kick_mode": "interactive",
    "model": "cloud_default",
    "effort": "medium",
    "review": "codex",
    "design": "skip",
    "plan": "skip",
}

AskFn = Callable[[dict[str, Any], dict[str, Any], str], Any]


def _norm_key(key: object) -> str:
    return str(key).strip().lower()


def _as_criteria(raw: object, fallback: dict[str, str]) -> dict[str, str]:
    if not isinstance(raw, dict) or not raw:
        return dict(fallback)
    out: dict[str, str] = {}
    for key, text in raw.items():
        name = _norm_key(key)
        if not name:
            continue
        out[name] = str(text).strip() if text is not None else ""
    return out or dict(fallback)


def _pass_criteria(makers: dict[str, str], kind: str) -> dict[str, str]:
    """skip, or a role id. Parallel questions cannot say 'same as maker'."""
    out = {
        "skip": f"No separate {kind} pass; the kick brief is enough.",
    }
    for key, text in makers.items():
        if key == "skip":
            continue
        out[key] = f"Assign the {kind} pass to {key}. {text}"
    return out


def _review_criteria(raw: object) -> dict[str, str]:
    merged = _as_criteria(raw, DEFAULT_REVIEW)
    # Hard policy: Review is not Pooh and not the orchestrator.
    cleaned = {key: text for key, text in merged.items() if key not in FORBIDDEN_REVIEW}
    return cleaned or dict(DEFAULT_REVIEW)


def resolve_criteria(cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Criteria maps for the seven Choice questions. Config overrides win."""
    block = cfg.get("maker_pick") or {}
    makers = _as_criteria(block.get("makers"), DEFAULT_MAKERS)
    design = block.get("design")
    plan = block.get("plan")
    return {
        "makers": makers,
        "kick_modes": _as_criteria(block.get("kick_modes"), DEFAULT_KICK_MODES),
        "models": _as_criteria(block.get("models"), DEFAULT_MODELS),
        "efforts": _as_criteria(block.get("efforts"), DEFAULT_EFFORTS),
        "review": _review_criteria(block.get("review")),
        "design": _as_criteria(design, _pass_criteria(makers, "design"))
        if design
        else _pass_criteria(makers, "design"),
        "plan": _as_criteria(plan, _pass_criteria(makers, "plan"))
        if plan
        else _pass_criteria(makers, "plan"),
    }


def _field_options(criteria: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        "choice": criteria["makers"],
        "kick_mode": criteria["kick_modes"],
        "model": criteria["models"],
        "effort": criteria["efforts"],
        "review": criteria["review"],
        "design": criteria["design"],
        "plan": criteria["plan"],
    }


def resolve_defaults(cfg: dict[str, Any], criteria: dict[str, dict[str, str]]) -> dict[str, str]:
    """Fail-open answers. Each value is forced into that field's option set."""
    block = cfg.get("maker_pick") or {}
    override = block.get("defaults") or {}
    options = _field_options(criteria)
    resolved: dict[str, str] = {}
    for field in OUTPUT_FIELDS:
        allowed = options[field]
        candidate = FAIL_OPEN_DEFAULTS[field]
        if isinstance(override, dict) and override.get(field):
            candidate = _norm_key(override[field])
        if candidate not in allowed:
            candidate = next(iter(allowed))
        resolved[field] = candidate
    return resolved


def _threshold(cfg: dict[str, Any]) -> float:
    block = cfg.get("maker_pick") or {}
    if block.get("min_choice_confidence") is not None:
        return float(block["min_choice_confidence"])
    return float((cfg.get("thresholds") or {}).get("min_choice_confidence", 0.55))


def build_questions(criteria: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Build Choice questions. Importing the SDK waits until a live call."""
    from typesafe_sdk import Choice

    return {
        "maker": Choice(
            instructions=(
                "Which implementer should take this kick? "
                "Use `goal`, `kind_hint`, `stack`, `repo`, and `constraints`."
            ),
            criteria=criteria["makers"],
        ),
        "kick_mode": Choice(
            instructions=(
                "How should this implement kick run: interactive, background, or skip?"
            ),
            criteria=criteria["kick_modes"],
        ),
        "implement_model": Choice(
            instructions=(
                "Which implement model should the kick use? "
                "`cloud_default` means the cloud agent's configured default. "
                "Every other option is an explicit model id."
            ),
            criteria=criteria["models"],
        ),
        "effort": Choice(
            instructions="How much implement effort does this kick justify?",
            criteria=criteria["efforts"],
        ),
        "review": Choice(
            instructions=(
                "Who should review the kick result? "
                "Review is not the orchestrator. Never assign Pooh or the orchestrator. "
                "Pick a review role, or skip."
            ),
            criteria=criteria["review"],
        ),
        "design": Choice(
            instructions=(
                "Should a separate design pass run before implementation, and if so which role owns it? "
                "Choose skip or one assignee role."
            ),
            criteria=criteria["design"],
        ),
        "plan": Choice(
            instructions=(
                "Should a separate planning pass run before implementation, and if so which role owns it? "
                "Choose skip or one assignee role."
            ),
            criteria=criteria["plan"],
        ),
    }


def _state_for_jev(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "goal": state.get("goal") or state.get("raw") or "",
        "kind_hint": state.get("kind") or state.get("kind_hint") or "unknown",
        "stack": state.get("stack") or "",
        "repo": state.get("repo") or "",
        "constraints": state.get("constraints") or "",
        "notes": str(state.get("notes") or "")[:500],
        "risk": state.get("risk") or "",
    }


def _probs(choice_obj: Any) -> dict[str, float]:
    raw = getattr(choice_obj, "probabilities", None) or {}
    try:
        items = raw.items()
    except AttributeError:
        return {}
    return {str(key): round(float(value), 4) for key, value in items}


def _policy(cfg: dict[str, Any], *, honor_choice: bool) -> dict[str, Any]:
    return {
        "honor_choice": honor_choice,
        "no_llm_reargue": True,
        "honor_in_active_mode": True,
        "shadow_mode_is_advisory": cfg.get("mode") == "shadow",
        "review_excludes": sorted(FORBIDDEN_REVIEW),
    }


def _payload(
    *,
    values: dict[str, str],
    confidence: dict[str, float],
    probabilities: dict[str, dict[str, float]],
    below_threshold: list[str],
    jev_used: bool,
    fail_open: bool,
    reason: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {field: values[field] for field in OUTPUT_FIELDS}
    out.update(
        {
            "confidence": confidence,
            "probabilities": probabilities,
            "below_threshold": below_threshold,
            "jev_used": jev_used,
            "fail_open": fail_open,
            "reason": reason,
            "mode": cfg.get("mode"),
            "policy": _policy(cfg, honor_choice=not fail_open),
        }
    )
    return out


def _log(cfg: dict[str, Any], goal: str, out: dict[str, Any]) -> None:
    log_run(
        resolve_log_path(cfg),
        {
            "event": "maker_pick",
            "goal": goal[:300],
            "choice": out["choice"],
            "kick_mode": out["kick_mode"],
            "model": out["model"],
            "effort": out["effort"],
            "review": out["review"],
            "design": out["design"],
            "plan": out["plan"],
            "confidence": out["confidence"],
            "below_threshold": out["below_threshold"],
            "fail_open": out["fail_open"],
            "jev_used": out["jev_used"],
            "reason": out["reason"],
            "mode": out["mode"],
        },
    )


def _fail_open(cfg: dict[str, Any], criteria: dict[str, dict[str, str]], goal: str, reason: str) -> dict[str, Any]:
    values = resolve_defaults(cfg, criteria)
    out = _payload(
        values=values,
        confidence={field: 0.0 for field in OUTPUT_FIELDS},
        probabilities={field: {} for field in OUTPUT_FIELDS},
        below_threshold=[],
        jev_used=False,
        fail_open=True,
        reason=reason,
        cfg=cfg,
    )
    _log(cfg, goal, out)
    return out


def _ask_jev(jstate: dict[str, Any], questions: dict[str, Any], model: str) -> Any:
    from src.jev_client import system_one

    return system_one(jstate, questions, model=model)


def roster_from_result(
    result: Any,
    *,
    cfg: dict[str, Any],
    criteria: dict[str, dict[str, str]],
    goal: str,
) -> dict[str, Any]:
    """Map a system_one result onto one-pager fields. Does not call a model."""
    threshold = _threshold(cfg)
    options = _field_options(criteria)
    defaults = resolve_defaults(cfg, criteria)
    values: dict[str, str] = {}
    confidence: dict[str, float] = {}
    probabilities: dict[str, dict[str, float]] = {}
    below: list[str] = []
    choices = getattr(result, "choices", {}) or {}
    review_guarded = False

    for question_id, field in QUESTION_TO_FIELD.items():
        picked = choices.get(question_id) if hasattr(choices, "get") else None
        raw_choice = _norm_key(getattr(picked, "choice", "") or "")
        allowed = options[field]
        # Review can never surface the orchestrator, even if a stub returns it.
        if field == "review" and raw_choice in FORBIDDEN_REVIEW:
            raw_choice = ""
            review_guarded = True
        # A dropped or unknown option is not a Jev answer. Keep a legal default
        # and do not reuse the rejected option's confidence.
        substituted = raw_choice not in allowed
        if substituted:
            raw_choice = defaults[field]
        conf = 0.0 if substituted else round(float(getattr(picked, "confidence", 0.0) or 0.0), 4)
        values[field] = raw_choice
        confidence[field] = conf
        probabilities[field] = {} if substituted or picked is None else _probs(picked)
        if conf < threshold:
            below.append(field)

    reason = "jev choices"
    if below:
        reason = "jev choices; below_threshold: " + ",".join(below)
    if review_guarded:
        reason = reason + "; review excluded orchestrator"
    out = _payload(
        values=values,
        confidence=confidence,
        probabilities=probabilities,
        below_threshold=below,
        jev_used=True,
        fail_open=False,
        reason=reason,
        cfg=cfg,
    )
    _log(cfg, goal, out)
    return out


def maker_pick(
    state: dict[str, Any],
    *,
    ask: AskFn | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return maker, kick mode, and roster fields for one kick cycle.

    ``ask`` is the Jev call. Tests pass a stub. Production uses ``system_one``.
    A disabled router, a bypass marker, or a Jev failure returns documented
    defaults with ``fail_open: true``. A low-confidence Choice is still returned
    and listed in ``below_threshold``; it is not replaced and not re-asked.
    """
    cfg = cfg if cfg is not None else load_config()
    criteria = resolve_criteria(cfg)
    goal = str(state.get("goal") or state.get("raw") or "")

    if not cfg.get("enabled", True) or _bypassed(state):
        return _fail_open(cfg, criteria, goal, "lab disabled or bypass jev")

    jstate = _state_for_jev(state)
    model = str(cfg.get("model") or "jev-latest")
    caller = ask or _ask_jev
    try:
        questions = build_questions(criteria)
        result = caller(jstate, questions, model)
    except (Exception, SystemExit):
        # Missing API key raises SystemExit. Exception text can echo request state,
        # so the returned reason stays generic and the log stores no traceback.
        return _fail_open(cfg, criteria, goal, "jev unavailable; fail-open defaults")
    return roster_from_result(result, cfg=cfg, criteria=criteria, goal=goal)
