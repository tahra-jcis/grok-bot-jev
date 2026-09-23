"""Kick-roster Jev routing with box-compatible maker selection behavior.

This reconciles two needs in one ``system_one`` call:
1) Preserve box production maker-pick behavior (Fugu makers, residual handling,
   banned filtering, prefer_local fail-open bias, review self-check guardrails,
   urgency score, and forced defer when residuals are empty and urgency is low).
2) Keep roster fields for one-pagers (`model`, `effort`, `review`, `design`,
   and `plan`) without asking a second model to re-argue Jev choices.
"""

from __future__ import annotations

from typing import Any, Callable

from src.config import load_config, resolve_log_path
from src.logger import log_run

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

# Question id → JSON field for Choice questions only.
QUESTION_TO_FIELD = {
    "maker": "choice",
    "kick_mode": "kick_mode",
    "implement_model": "model",
    "effort": "effort",
    "review": "review",
    "design": "design",
    "plan": "plan",
}

FORBIDDEN_REVIEW = frozenset({"pooh", "orchestrator"})
NON_ASSIGNABLE_MAKERS = frozenset({"defer", "ask_human"})
BYPASS_MARKERS = ("bypass jev", "bypass jev:", "no jev")

ROLE_HINTS = {
    "impl": "Implementation / coding Maker kick",
    "review": "Code or design review (prefer different Maker than impl if self_review_forbidden)",
    "qa_view": "QA / verification viewpoint",
    "dev_view": "Dev architecture / design viewpoint",
    "research": "Research / investigation",
    "docs": "Documentation write-up",
}

DEFAULT_MAKERS = {
    "codex_fugu": "Long overnight implementation with high Codex Fugu residual.",
    "claude_fugu": "Long overnight implementation with high Claude Fugu residual.",
    "claude": "Plain Claude for short/medium interactive work.",
    "codex": "Plain Codex for short investigate/patch work.",
    "cursor": "Interactive local implementation or short Cursor Agent run.",
    "defer": "No usable residual yet; wait for refill or clearer ticket.",
    "ask_human": "Policy/legal ambiguity or unresolved self-review conflict.",
}

DEFAULT_KICK_MODES = {
    "interactive": "A person or parent stays in the loop during the kick.",
    "background": "Run the kick unattended and report when it finishes.",
    "skip": "Do not start an implement kick.",
}

# ``cloud_default`` or an explicit model id the kick platform accepts.
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
# prefer_local can still override ``choice`` to cursor.
FAIL_OPEN_DEFAULTS = {
    "choice": "defer",
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
    """Return skip + assignable role ids for design/plan."""
    out = {
        "skip": f"No separate {kind} pass; the kick brief is enough.",
    }
    for key, text in makers.items():
        if key in NON_ASSIGNABLE_MAKERS:
            continue
        out[key] = f"Assign the {kind} pass to {key}. {text}"
    return out


def _review_criteria(raw: object) -> dict[str, str]:
    merged = _as_criteria(raw, DEFAULT_REVIEW)
    cleaned = {key: text for key, text in merged.items() if key not in FORBIDDEN_REVIEW}
    return cleaned or dict(DEFAULT_REVIEW)


def resolve_criteria(cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Criteria maps for all questions. Config overrides win."""
    block = cfg.get("maker_pick") or {}
    makers = _as_criteria(block.get("makers"), DEFAULT_MAKERS)
    # Keep a safe fallback even when config accidentally drops defer/ask_human.
    makers.setdefault("defer", DEFAULT_MAKERS["defer"])
    makers.setdefault("ask_human", DEFAULT_MAKERS["ask_human"])
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


def _norm_residual(val: Any) -> str:
    if val is None:
        return "empty"
    if isinstance(val, (int, float)):
        n = float(val)
        if n <= 0:
            return "empty"
        if n < 25:
            return "low"
        if n < 60:
            return "med"
        return "high"
    s = str(val).strip().lower()
    if s in {"", "none", "empty", "0", "zero"}:
        return "empty"
    if s in {"low", "med", "medium", "high"}:
        return "med" if s == "medium" else s
    return s


def _normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    residuals_in = state.get("residuals") or {}
    if not isinstance(residuals_in, dict):
        residuals_in = {}
    residuals = {
        k: _norm_residual(residuals_in.get(k))
        for k in ("codex_fugu", "claude_fugu", "claude", "codex", "cursor")
    }
    for key, val in residuals_in.items():
        if key not in residuals:
            residuals[key] = _norm_residual(val)

    banned = state.get("banned") or []
    if isinstance(banned, str):
        banned = [part.strip() for part in banned.split(",") if part.strip()]
    banned = [_norm_key(part).replace("-", "_") for part in banned if str(part).strip()]

    role = _norm_key(state.get("role") or "impl")
    if role not in ROLE_HINTS:
        role = "impl"

    return {
        "role": role,
        "role_hint": ROLE_HINTS.get(role, role),
        "task_hint": str(state.get("task_hint") or state.get("goal") or "")[:500],
        "goal": str(state.get("goal") or state.get("raw") or ""),
        "kind_hint": state.get("kind") or state.get("kind_hint") or "unknown",
        "stack": state.get("stack") or "",
        "repo": state.get("repo") or "",
        "constraints": str(state.get("constraints") or ""),
        "notes": str(state.get("notes") or "")[:500],
        "risk": state.get("risk") or "",
        "residuals": residuals,
        "banned": banned,
        "prefer_local": bool(state.get("prefer_local", False)),
        "self_review_forbidden": bool(state.get("self_review_forbidden", True)),
        "prior_maker": _norm_key(state.get("prior_maker") or state.get("impl_maker") or ""),
        "duration_hint": _norm_key(state.get("duration_hint") or state.get("duration") or "unknown"),
    }


def _bypassed(state: dict[str, Any]) -> bool:
    raw = " ".join(
        str(state.get(key, ""))
        for key in ("goal", "raw", "user_message", "notes", "task_hint")
    ).lower()
    return any(marker in raw for marker in BYPASS_MARKERS)


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
    """Fail-open answers, forced into legal per-field options."""
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


def _apply_policy_filters(
    criteria: dict[str, dict[str, str]],
    jstate: dict[str, Any],
) -> tuple[dict[str, dict[str, str]], bool]:
    """Apply banned and review-self rules to choice criteria before asking Jev."""
    review_guarded = False
    out = {name: dict(values) for name, values in criteria.items()}
    banned = set(jstate["banned"])
    prior = _norm_key(jstate["prior_maker"])

    for key in list(out["makers"]):
        if key in NON_ASSIGNABLE_MAKERS:
            continue
        if key in banned:
            del out["makers"][key]

    if jstate["self_review_forbidden"] and jstate["role"] == "review" and prior:
        if prior in out["makers"] and prior not in NON_ASSIGNABLE_MAKERS:
            del out["makers"][prior]
            review_guarded = True

    for field in ("design", "plan", "review"):
        for key in list(out[field]):
            if key == "skip":
                continue
            if key in banned:
                del out[field][key]
                review_guarded = True
            elif jstate["self_review_forbidden"] and prior and key == prior:
                del out[field][key]
                review_guarded = True

    for forbidden in FORBIDDEN_REVIEW:
        if forbidden in out["review"]:
            del out["review"][forbidden]
            review_guarded = True

    # Keep each field answerable.
    out["makers"].setdefault("defer", criteria["makers"].get("defer", DEFAULT_MAKERS["defer"]))
    out["makers"].setdefault(
        "ask_human",
        criteria["makers"].get("ask_human", DEFAULT_MAKERS["ask_human"]),
    )
    if not out["review"]:
        out["review"] = {"skip": DEFAULT_REVIEW["skip"]}
    for field in ("design", "plan"):
        if not out[field]:
            out[field] = {"skip": f"No separate {field} pass; the kick brief is enough."}
    return out, review_guarded


def build_questions(criteria: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Build all Choice/Score questions. SDK import waits until a live call."""
    try:
        from typesafe_sdk import Choice, Score
    except Exception:
        # Offline tests can still verify policy wiring without the SDK package.
        class Choice:  # type: ignore[no-redef]
            def __init__(self, *, instructions: str, criteria: dict[str, str]) -> None:
                self.instructions = instructions
                self.criteria = criteria

        class Score:  # type: ignore[no-redef]
            def __init__(self, *, instructions: str, criteria: list[str]) -> None:
                self.instructions = instructions
                self.criteria = criteria

    return {
        "maker": Choice(
            instructions=(
                "Pick the single best Maker to kick for this Loop task. "
                "Honor residuals: empty/low means avoid that Maker when possible. "
                "Long overnight implementation leans toward *_fugu when residual is med/high. "
                "Short interactive work can use cursor/claude/codex. "
                "No usable residual or can wait: defer. "
                "Policy conflict or legal ambiguity: ask_human. "
                "If self_review_forbidden and role=review, avoid prior_maker."
            ),
            criteria=criteria["makers"],
        ),
        "urgency": Score(
            instructions="How urgently should we kick now instead of deferring?",
            criteria=[
                "Can wait — defer is acceptable",
                "Normal urgency — kick soon",
                "Urgent — kick immediately when residual allows",
            ],
        ),
        "kick_mode": Choice(
            instructions="How should this implement kick run: interactive, background, or skip?",
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
                "Never assign Pooh or the orchestrator. Pick a review role, or skip."
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


def _state_for_jev(jstate: dict[str, Any]) -> dict[str, Any]:
    return {
        "goal": jstate["goal"],
        "kind_hint": jstate["kind_hint"],
        "stack": jstate["stack"],
        "repo": jstate["repo"],
        "constraints": jstate["constraints"],
        "notes": jstate["notes"],
        "risk": jstate["risk"],
        "role": jstate["role"],
        "role_hint": jstate["role_hint"],
        "task_hint": jstate["task_hint"],
        "residuals": jstate["residuals"],
        "banned": jstate["banned"],
        "prefer_local": jstate["prefer_local"],
        "self_review_forbidden": jstate["self_review_forbidden"],
        "prior_maker": jstate["prior_maker"],
        "duration_hint": jstate["duration_hint"],
    }


def _probs(choice_obj: Any) -> dict[str, float]:
    raw = getattr(choice_obj, "probabilities", None) or {}
    try:
        items = raw.items()
    except AttributeError:
        return {}
    return {str(key): round(float(value), 4) for key, value in items}


def _policy(cfg: dict[str, Any], *, honor_choice: bool, fail_open: bool) -> dict[str, Any]:
    return {
        "honor_choice": honor_choice,
        "no_llm_reargue": True,
        "llm_must_not_reargue": True,
        "honor_in_active_mode": True,
        "fail_open": fail_open,
        "shadow_mode_is_advisory": cfg.get("mode") == "shadow",
        "review_excludes": sorted(FORBIDDEN_REVIEW),
    }


def _payload(
    *,
    values: dict[str, str],
    confidence: dict[str, float],
    probabilities: dict[str, dict[str, float]],
    below_threshold: list[str],
    urgency_0_1: float,
    jev_used: bool,
    fail_open: bool,
    reason: str,
    cfg: dict[str, Any],
    details: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {field: values[field] for field in OUTPUT_FIELDS}
    out.update(
        {
            "confidence": confidence,
            "probabilities": probabilities,
            "below_threshold": below_threshold,
            "urgency_0_1": round(float(urgency_0_1), 4),
            "jev_used": jev_used,
            "fail_open": fail_open,
            "reason": reason,
            "mode": cfg.get("mode"),
            "details": details,
            "policy": _policy(cfg, honor_choice=not fail_open, fail_open=fail_open),
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
            "urgency_0_1": out["urgency_0_1"],
            "confidence": out["confidence"],
            "below_threshold": out["below_threshold"],
            "fail_open": out["fail_open"],
            "jev_used": out["jev_used"],
            "reason": out["reason"],
            "mode": out["mode"],
        },
    )


def _fail_open(
    cfg: dict[str, Any],
    criteria: dict[str, dict[str, str]],
    jstate: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    values = resolve_defaults(cfg, criteria)
    if jstate.get("prefer_local") and "cursor" in criteria["makers"]:
        values["choice"] = "cursor"
    details = {
        "residuals": jstate["residuals"],
        "banned": jstate["banned"],
        "prefer_local": jstate["prefer_local"],
        "self_review_forbidden": jstate["self_review_forbidden"],
        "prior_maker": jstate["prior_maker"],
        "role": jstate["role"],
    }
    out = _payload(
        values=values,
        confidence={field: 0.0 for field in OUTPUT_FIELDS},
        probabilities={field: {} for field in OUTPUT_FIELDS},
        below_threshold=[],
        urgency_0_1=0.0,
        jev_used=False,
        fail_open=True,
        reason=reason,
        cfg=cfg,
        details=details,
    )
    _log(cfg, jstate["goal"], out)
    return out


def _ask_jev(jstate: dict[str, Any], questions: dict[str, Any], model: str) -> Any:
    from src.jev_client import system_one

    return system_one(jstate, questions, model=model)


def roster_from_result(
    result: Any,
    *,
    cfg: dict[str, Any],
    criteria: dict[str, dict[str, str]],
    jstate: dict[str, Any],
    review_guarded: bool,
) -> dict[str, Any]:
    """Map one ``system_one`` result to kick roster output."""
    threshold = _threshold(cfg)
    options = _field_options(criteria)
    defaults = resolve_defaults(cfg, criteria)
    values: dict[str, str] = {}
    confidence: dict[str, float] = {}
    probabilities: dict[str, dict[str, float]] = {}
    below: list[str] = []
    choices = getattr(result, "choices", {}) or {}

    for question_id, field in QUESTION_TO_FIELD.items():
        picked = choices.get(question_id) if hasattr(choices, "get") else None
        raw_choice = _norm_key(getattr(picked, "choice", "") or "")
        allowed = options[field]
        # Review can never surface the orchestrator, even if a stub returns it.
        if field == "review" and raw_choice in FORBIDDEN_REVIEW:
            raw_choice = ""
            review_guarded = True
        substituted = raw_choice not in allowed
        if substituted:
            raw_choice = defaults[field]
        conf = 0.0 if substituted else round(float(getattr(picked, "confidence", 0.0) or 0.0), 4)
        values[field] = raw_choice
        confidence[field] = conf
        probabilities[field] = {} if substituted or picked is None else _probs(picked)
        if conf < threshold:
            below.append(field)

    scores = getattr(result, "scores", {}) or {}
    urgency_obj = scores.get("urgency") if hasattr(scores, "get") else None
    urgency_raw = float(getattr(urgency_obj, "score", 0.0) or 0.0)
    urgency_0_1 = max(0.0, min(1.0, urgency_raw / 2.0))
    all_empty = all(
        jstate["residuals"].get(key, "empty") == "empty"
        for key in ("codex_fugu", "claude_fugu", "claude", "codex", "cursor")
    )
    forced_defer = (
        values["choice"] not in NON_ASSIGNABLE_MAKERS
        and all_empty
        and urgency_0_1 < 0.6
    )
    if forced_defer and "defer" in options["choice"]:
        values["choice"] = "defer"

    reason = "jev choices"
    if below:
        reason = "jev choices; below_threshold: " + ",".join(below)
    if review_guarded:
        reason = reason + "; review excluded orchestrator or filtered role"
    if forced_defer:
        reason = f"forced_defer: all residuals empty and urgency={urgency_0_1:.2f}"

    details = {
        "maker": values["choice"],
        "residuals": jstate["residuals"],
        "banned": jstate["banned"],
        "prefer_local": jstate["prefer_local"],
        "self_review_forbidden": jstate["self_review_forbidden"],
        "prior_maker": jstate["prior_maker"],
        "role": jstate["role"],
    }
    out = _payload(
        values=values,
        confidence=confidence,
        probabilities=probabilities,
        below_threshold=below,
        urgency_0_1=urgency_0_1,
        jev_used=True,
        fail_open=False,
        reason=reason,
        cfg=cfg,
        details=details,
    )
    _log(cfg, jstate["goal"], out)
    return out


def maker_pick(
    state: dict[str, Any],
    *,
    ask: AskFn | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return maker + kick roster fields for one kick cycle.

    ``ask`` is the Jev call. Tests pass a stub. Production uses ``system_one``.
    Disabled router, bypass marker, and Jev failures return fail-open defaults
    with every field present (`fail_open: true`).
    """
    cfg = cfg if cfg is not None else load_config()
    base_criteria = resolve_criteria(cfg)
    jstate = _normalize_state(state)
    criteria, review_guarded = _apply_policy_filters(base_criteria, jstate)

    if not cfg.get("enabled", True) or _bypassed(state):
        return _fail_open(cfg, criteria, jstate, "lab disabled or bypass jev")

    model = str(cfg.get("model") or "jev-latest")
    caller = ask or _ask_jev
    try:
        questions = build_questions(criteria)
        result = caller(_state_for_jev(jstate), questions, model)
    except (Exception, SystemExit):
        # Missing API key raises SystemExit. Keep reason generic to avoid leaks.
        return _fail_open(cfg, criteria, jstate, "jev unavailable; fail-open defaults")
    return roster_from_result(
        result,
        cfg=cfg,
        criteria=criteria,
        jstate=jstate,
        review_guarded=review_guarded,
    )


def pick_maker(state: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias for box integrations."""
    return maker_pick(state)
