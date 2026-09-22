#!/usr/bin/env python3
"""CLI for Jev-backed workflow selection.

    python3 -m wfrouter "the sword swing feels weightless, can we talk about it"
    python3 -m wfrouter --stage generate "make the goblin portraits"
    python3 -m wfrouter --context art_bible=written "what art is still missing"
    python3 -m wfrouter --list
    python3 -m wfrouter --list --stage generate
    python3 -m wfrouter --show coding-game
    echo "long request" | python3 -m wfrouter

Reads JEV_API_KEY from .env. --list and --show make no network call.
"""
import argparse
import json
import sys

from . import JevError, RegistryError, load_registry, select_workflow
from . import registry as R


def print_registry(stage=None):
    reg = load_registry()
    mods = [m for m in R.enabled(reg) if not stage or m["stage"] == stage]
    order = reg["stages"]
    done = sum(R.implemented(m) for m in mods)
    print(f"{len(mods)} workflows in {len({m['stage'] for m in mods})} stages, "
          f"{done} implemented\n")
    for st in sorted({m['stage'] for m in mods}, key=lambda s: order[s]["order"]):
        print(f"{st}  -- {order[st]['description']}")
        for m in [m for m in mods if m["stage"] == st]:
            mark = "" if R.implemented(m) else "  [interface only]"
            print(f"  {m['command']:<22} {m['display_name']}{mark}")
            print(f"  {'':<22} {m['description'].split('.')[0]}.")
        print()


def print_module(workflow_id):
    reg = load_registry()
    m = R.by_id(reg, workflow_id.lstrip("/"))
    if m is None:
        print(f"no such workflow: {workflow_id}", file=sys.stderr)
        return 1
    print(json.dumps(R.interface(m, reg), indent=2, ensure_ascii=False))
    return 0


def main():
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("request", nargs="*", help="the request to route (or pipe on stdin)")
    p.add_argument("--stakes", default="medium", choices=["low", "medium", "high"])
    p.add_argument("--stage", help="restrict to one stage")
    p.add_argument("--allow", help="comma-separated workflow ids or commands")
    p.add_argument("--context", action="append", metavar="KEY=VALUE",
                   help="what is already true; repeatable")
    p.add_argument("--two-step", dest="two_step", action="store_true",
                   default=None, help="choose the stage first, then the workflow")
    p.add_argument("--one-shot", dest="two_step", action="store_false",
                   help="force a single question over every workflow")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--list", action="store_true", help="show the workflow registry")
    p.add_argument("--show", metavar="ID", help="show one workflow's interface")
    args = p.parse_args()

    try:
        if args.show:
            return print_module(args.show)
        if args.list:
            print_registry(args.stage)
            return 0
    except RegistryError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    request = " ".join(args.request).strip()
    if not request and not sys.stdin.isatty():
        request = sys.stdin.read().strip()
    if not request:
        p.print_usage()
        return 1

    context = {}
    for pair in args.context or []:
        k, _, v = pair.partition("=")
        context[k.strip()] = v.strip()

    try:
        route = select_workflow(
            request,
            context=context or None,
            stage=args.stage,
            allow=[x.strip() for x in args.allow.split(",")] if args.allow else None,
            stakes=args.stakes,
            two_step=args.two_step,
        )
    except (JevError, RegistryError, ValueError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(route.as_dict(), indent=2, ensure_ascii=False))
        return 0

    iface = route.interface
    if len(route.steps) > 1:
        path = " -> ".join(f"{s['decision']} ({s['confidence']})"
                           for s in route.steps)
        print(f"route:      {path}")
    print(f"workflow:   {route.display_name}  ({route.workflow_id})")
    print(f"command:    {route.command}")
    print(f"stage:      {route.stage}")
    print(f"entry:      {iface['entry'].get('kind')} "
          f"{iface['entry'].get('ref') or '(unimplemented)'}")
    print(f"dir:        {iface['dir']}")
    if iface["inputs"]:
        print(f"inputs:     {', '.join(iface['inputs'])}")
    if iface["next_steps"]:
        print(f"next:       {', '.join('/' + n for n in iface['next_steps'])}")
    print(f"confidence: {route.confidence}")
    for wid, prob in sorted(route.probabilities.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  {prob:>5.2f}  {wid}")
    if route.guidance:
        print(f"guidance:   {route.guidance}")
    if route.blocked_by:
        print(f"blocked by: {', '.join('/' + b for b in route.blocked_by)}  "
              f"(you reported these as not done)")
    if not route.implemented:
        print(f"note:       nothing is implemented behind {route.command} yet; "
              f"write {iface['entry'].get('system_prompt')} in the directory above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
