# Jev Usage Router for Grok Bot

Use TypeSafe Jev as a cheap decision layer before expensive work. This skill is a policy and integration recipe, not a hidden system hook.

## Workflow

1. Check the kill switch first. If the configuration has `enabled: false`, or the user says `bypass jev` or `no jev`, skip Jev and proceed normally.
2. Before opening a browser, doing multi-source research, retrying a failed approach, loading several specialized skills, or launching a subagent, build a compact JSON state:

```json
{
  "goal": "what the user wants",
  "kind": "chat|lookup|research|browser|coding|write|account",
  "cached_artifact": false,
  "cached_note": "optional freshness and scope",
  "prior_error": "optional last error",
  "same_error_count": 0,
  "sources_found": 0,
  "constraints": "optional limits"
}
```

3. Invoke the installed router with that state and read the returned `action`, `reason`, `mode`, and `details`.
4. In `shadow` mode, record the recommendation but continue according to normal Grok Bot judgment. In `active` mode, honor the actions below.

## Action policy in active mode

- `reuse_cache`: use the fresh artifact; do not repeat the expensive work.
- `stop_retry`: stop the repeated approach, explain the failure, and propose a different path or ask the user.
- `run_deterministic`: perform the known, bounded lookup without broad research.
- `chat_only`: answer directly without tools.
- `ask_human`: pause before any account, send, publish, pay, delete, or permission-changing action.
- `allow_subagent`: use a specialized subagent only when it is actually available and appropriate.
- `research_capped`: research with at most `details.max_browser_sources` sources/pages, then synthesize.
- `proceed_full`: continue with normal work while still applying ordinary safety and confirmation rules.

Never treat a Jev result as permission to reveal secrets or bypass a separate safety requirement. Do not include API keys in state, prompts, logs, or tool arguments. Keep goals and notes short and redact sensitive user content before logging.

## Kick one-pager (`maker-pick`)

Before writing a kick one-pager, call `maker-pick` once and copy the JSON fields. Do not fill these with the placeholder `default`, and do not run another model to second-guess the Choice.

```bash
python -m src.cli maker-pick '{"goal":"what the kick implements","kind":"coding","stack":"optional","repo":"optional","constraints":"optional","risk":"optional"}'
```

| JSON field | Write on the one-pager |
| --- | --- |
| `choice` | Implement maker (`cursor`, `codex`, `claude`, `grok`, or a configured role) |
| `kick_mode` | `interactive`, `background`, or `skip` |
| `model` | `cloud_default` or the explicit model id |
| `effort` | `low`, `medium`, `high`, or `xhigh` |
| `review` | Review assignee. Never Pooh and never the orchestrator |
| `design` | `skip` or the design role |
| `plan` | `skip` or the plan role |

Honor `choice` and the other fields as returned. If `fail_open` is true, the router used documented defaults because Jev did not answer; say that on the one-pager. If a field is in `below_threshold`, still show that Choice and mark it low-confidence. `shadow` mode is advisory. `active` mode uses the returned roster. This command does not start the kick.

## Honest integration limits

Grok Bot has to wake and invoke this router; this skill provides no pre-wake hook and cannot reduce the cost of waking the bot. Shadow mode is advisory. Active mode is enforceable only insofar as the Grok Bot skill runtime follows this policy. Jev can misclassify or be unavailable, so preserve a safe fallback and use the kill switch when needed.
