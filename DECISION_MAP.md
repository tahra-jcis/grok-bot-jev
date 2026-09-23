# Decision map

Question ids stay inside the router. One-pagers read the JSON fields.

`maker-pick` asks all of these in one TypeSafe `system_one` call. Code honors `choice` directly and never sends Jev's answer to another model for re-argument.

Low confidence does not rewrite a Choice. The field keeps Jev's option and is listed in `below_threshold` when confidence is under `thresholds.min_choice_confidence` (default `0.55`, override `maker_pick.min_choice_confidence`). `fail_open: true` is only for a disabled router, a `bypass jev` / `no jev` marker, or a Jev call that failed. Those paths fill every field from `maker_pick.defaults`, or the built-in defaults below.

Maker criteria is filtered **before** Choice:

- `banned` makers are removed from maker/design/plan/review options.
- `self_review_forbidden + prior_maker` removes `prior_maker` for review-role picks.
- `review` can never be `pooh` or `orchestrator`.
- `residuals` are normalized to `empty|low|med|high` and included in Jev state/output details.
- `urgency` uses a Score question (`urgency_0_1`). If all tracked residuals are `empty` and urgency is low, `choice` is forced to `defer`.

## maker-pick

| Question id | JSON field | Built-in options | One-pager meaning |
| --- | --- | --- | --- |
| `maker` | `choice` | `codex_fugu`, `claude_fugu`, `claude`, `codex`, `cursor`, `defer`, `ask_human` | Who implements |
| `urgency` | `urgency_0_1` | Score 0..2, normalized to 0..1 | How urgent it is to kick now |
| `kick_mode` | `kick_mode` | `interactive`, `background`, `skip` | How the kick runs |
| `implement_model` | `model` | `cloud_default`, `grok-4.7-high`, `gpt-5.6-sol-medium`, `claude-sonnet-5-thinking-medium` | Cloud default or an explicit model id |
| `effort` | `effort` | `low`, `medium`, `high`, `xhigh` | Implement effort |
| `review` | `review` | `codex`, `qa`, `security`, `skip` | Review assignee. Never `pooh` or `orchestrator` |
| `design` | `design` | `skip` or a maker role id | Separate design pass, or skip |
| `plan` | `plan` | `skip` or a maker role id | Separate plan pass, or skip |

Built-in fail-open defaults: `choice=defer`, `kick_mode=interactive`, `model=cloud_default`, `effort=medium`, `review=codex`, `design=skip`, `plan=skip`. If `prefer_local=true`, fail-open `choice` is biased to `cursor`.

Config keys under `maker_pick` replace a map when set: `makers`, `kick_modes`, `models`, `efforts`, `review`, `design`, `plan`, `defaults`. Review keys `pooh` and `orchestrator` are dropped if a config map includes them. Explicit model ids belong in `maker_pick.models`; point that map at the ids the kick platform actually accepts.

Other JSON keys (additive, same call): `confidence`, `probabilities`, `below_threshold`, `urgency_0_1`, `jev_used`, `fail_open`, `reason`, `mode`, `details`, `policy`.

```bash
python -m src.cli maker-pick '{"goal":"Add roster fields to the kick one-pager","kind":"coding","constraints":"TypeSafe Choice only"}'
```

## route

Unchanged usage gate. A bare JSON argument still calls `route`.

| Question id | Kind | JSON location | Used for |
| --- | --- | --- | --- |
| `intent` | Choice | `details.intent` | `chat_only`, `run_deterministic`, `ask_human`, `research_capped` |
| `reuse_cache` | Noul | `details.reuse_cache` | `reuse_cache` |
| `needs_subagent` | Noul | `details.needs_subagent` | `allow_subagent` |
| `stop_retry` | Noul | `details.stop_retry` | `stop_retry` |
| `complexity` | Score | `details.complexity_0_1` | Logged effort signal |

Top-level route field remains `action`.
