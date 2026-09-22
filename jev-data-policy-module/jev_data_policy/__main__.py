#!/usr/bin/env python3
"""Classify how sensitive a piece of content is.

    python3 -m jev_data_policy "客戶 A123456789 的帳單地址是..."
    python3 -m jev_data_policy --min-confidence 0.7 "..."
    python3 -m jev_data_policy --json "..."
    python3 -m jev_data_policy --classes            # the ladder, no network call
    cat suspect.log | python3 -m jev_data_policy

Reads JEV_API_KEY (native) or OPENROUTER_API_KEY (openrouter) from .env.
"""
import argparse
import json
import sys

from .classify import PolicyError, classes_path, classify, load
from .client import JevError, setting_source


def print_classes():
    spec = load()
    path, source = classes_path()
    print(f"classes: {path}\n         (from {source})\n")
    for i, c in enumerate(spec["classes"]):
        print(f"  {i}  {c['id']}")
        print(f"     {c['description']}\n")
    return 0


def main():
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("content", nargs="*",
                   help="the content to classify (or pipe on stdin)")
    p.add_argument("--min-confidence", type=float, default=0.0, metavar="P",
                   help="below this, raise the answer to the most sensitive "
                        "class still in play")
    p.add_argument("--context", action="append", metavar="KEY=VALUE",
                   help="anything else Jev should weigh; repeatable")
    p.add_argument("--backend", choices=["native", "openrouter"],
                   help="override the backend for this run")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--classes", action="store_true",
                   help="show the class ladder and exit; makes no network call")
    args = p.parse_args()

    try:
        if args.classes:
            return print_classes()

        content = " ".join(args.content) if args.content else sys.stdin.read()
        context = dict(kv.split("=", 1) for kv in args.context) if args.context else None
        result = classify(content, context=context, backend=args.backend,
                          min_confidence=args.min_confidence)
    except (PolicyError, JevError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
        return 0

    backend, source = setting_source("BACKEND", "native")
    print(f"backend:    {backend}  (from {source})")
    print(f"content:    {result.content_bytes} bytes")
    print(f"class:      {result.data_class}")
    print(f"confidence: {result.confidence:.2f}")
    if result.escalated:
        print(f"            escalated from {result.escalated_from!r}: below "
              f"--min-confidence {args.min_confidence}")
    if result.probabilities:
        ranked = sorted(result.probabilities.items(), key=lambda kv: -kv[1])
        print("            " + "  ".join(f"{k}={v:.2f}" for k, v in ranked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
