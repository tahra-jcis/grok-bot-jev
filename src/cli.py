from __future__ import annotations

import json
import sys

from src.maker_pick import maker_pick
from src.router import route_task

USAGE = """\
Usage:
  python -m src.cli '<json state>'
  python -m src.cli route '<json state>'
  python -m src.cli maker-pick '<json state>'

route (default) returns a usage-gate action.
maker-pick returns one kick roster: choice, kick_mode, model, effort, review, design, plan.

Example:
  python -m src.cli maker-pick '{"goal":"Add roster fields to the kick one-pager","kind":"coding","constraints":"TypeSafe Choice only"}'
"""


def _usage() -> None:
    print(USAGE, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help", "help"}:
        _usage()
        return 0 if argv and argv[0] in {"-h", "--help", "help"} else 2

    command = "route"
    payload = argv[0]
    if argv[0] in {"route", "maker-pick"}:
        if len(argv) != 2:
            _usage()
            return 2
        command = argv[0]
        payload = argv[1]

    try:
        state = json.loads(payload)
    except json.JSONDecodeError:
        print("Expected a JSON object state.", file=sys.stderr)
        _usage()
        return 2
    if not isinstance(state, dict):
        print("Expected a JSON object state.", file=sys.stderr)
        return 2

    out = maker_pick(state) if command == "maker-pick" else route_task(state)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
