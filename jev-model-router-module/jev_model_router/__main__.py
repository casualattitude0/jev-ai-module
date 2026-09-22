#!/usr/bin/env python3
"""CLI for Jev-backed model selection.

    python3 select_model.py "refactor the billing retry logic across 40 files"
    python3 select_model.py --stakes low --priorities cost,latency "rename a var"
    python3 select_model.py --serve "explain this stack trace: ..."
    python3 select_model.py --list
    echo "long task text" | python3 select_model.py

Reads JEV_API_KEY from .env. --serve runs the chosen model through its own
agent CLI (`claude`, `codex`), which carries its own auth; no provider API key
is read here.
"""
import argparse
import json
import sys

from . import DispatchError, JevError, load_registry, select_model, serve
from .client import setting_source
from .registry import enabled, variants


def print_registry():
    backend, source = setting_source("BACKEND", "native")
    print(f"jev backend: {backend}  (from {source})\n")
    reg = load_registry()
    vs = variants(reg)
    print(f"{len(enabled(reg))} models, {len(vs)} routing options "
          f"(one per model+effort pair)\n")
    for m in enabled(reg):
        flag = "" if m.get("verified") else "  [unverified id]"
        ctx = f"{m['context_tokens']:,}" if m.get("context_tokens") else "unspecified"
        print(f"{m['display_name']:<20} {m['id']:<20} {m['tier']:<9} "
              f"context={ctx}{flag}")
        for v in (v for v in vs if v["model"]["id"] == m["id"]):
            print(f"    {v['id']:<28} cost={v['cost']:<6} latency={v['latency']}")
        print(f"  {m['jev_role']}\n")


def main():
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("task", nargs="*", help="the task to route (or pipe on stdin)")
    p.add_argument("--stakes", default="medium", choices=["low", "medium", "high"])
    p.add_argument("--priorities", help="comma-separated, e.g. cost,latency,quality")
    p.add_argument("--constraints", help="comma-separated hard limits")
    p.add_argument("--allow",
                   help="comma-separated variant ids (model@effort) or model ids")
    p.add_argument("--efforts", help="comma-separated effort levels to allow")
    p.add_argument("--min-context", type=int, metavar="TOKENS",
                   help="drop models with a known smaller context window")
    p.add_argument("--transport", choices=["cli"],
                   help="how to reach the model when serving (default cli)")
    p.add_argument("--input-tokens", type=int, metavar="N",
                   help="size of the real input; implies a context requirement")
    p.add_argument("--kind", choices=["code", "browser", "research", "writing",
                                      "general"],
                   help="skip the assessment call by naming the kind yourself")
    p.add_argument("--difficulty", type=float, metavar="0-3",
                   help="skip the assessment call by scoring difficulty yourself")
    p.add_argument("--no-assess", action="store_true",
                   help="route in one call, with no capability floor")
    p.add_argument("--serve", action="store_true", help="also call the chosen model")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--list", action="store_true", help="show the model registry")
    args = p.parse_args()

    if args.list:
        print_registry()
        return 0

    task = " ".join(args.task).strip()
    if not task and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    if not task:
        p.print_usage()
        return 1

    def split(s):
        return [x.strip() for x in s.split(",") if x.strip()] if s else None

    try:
        sel = select_model(
            task if not args.serve else task[:500],
            stakes=args.stakes,
            priorities=split(args.priorities),
            constraints=split(args.constraints),
            allow=split(args.allow),
            efforts=split(args.efforts),
            min_context_tokens=args.min_context,
            input_tokens=args.input_tokens,
            kind=args.kind,
            difficulty=args.difficulty,
            assess_first=not args.no_assess,
        )
    except (JevError, ValueError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if args.json and not args.serve:
        print(json.dumps(sel.as_dict(), indent=2))
        return 0

    backend, source = setting_source("BACKEND", "native")
    print(f"backend:    {backend}  (from {source})")
    if sel.assessment:
        a = sel.assessment
        print(f"difficulty: {a.difficulty}  ({a.kind}, ambiguous {a.ambiguous})")
    if sel.floor:
        parts = []
        if sel.floor.get("min_tier"):
            parts.append(f"tier>={sel.floor['min_tier']}")
        if sel.floor.get("min_effort"):
            parts.append(f"effort>={sel.floor['min_effort']}")
        for k, v in (sel.floor.get("require") or {}).items():
            parts.append(f"{k}>={v}")
        for k in sel.floor.get("require_published") or []:
            parts.append(f"publishes {k}")
        print(f"floor:      {', '.join(parts)}")
    for mid, why in sel.excluded.items():
        print(f"  excluded  {mid}: {why}")
    ctx = sel.model.get("context_tokens")
    print(f"model:      {sel.display_name}  ({sel.model_id})")
    print(f"effort:     {sel.effort}")
    print(f"variant:    {sel.variant_id}")
    print(f"tier:       {sel.model['tier']} via {sel.model['provider']}  "
          f"cost={sel.cost} latency={sel.latency} "
          f"context={f'{ctx:,}' if ctx else 'unspecified'}")
    print(f"confidence: {sel.confidence}")
    for vid, prob in sorted(sel.probabilities.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  {prob:>5.2f}  {vid}")
    if sel.guidance:
        print(f"guidance:   {sel.guidance}")

    if args.serve:
        print("--- reply ---")
        try:
            print(serve(sel, task, transport=args.transport))
        except DispatchError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
