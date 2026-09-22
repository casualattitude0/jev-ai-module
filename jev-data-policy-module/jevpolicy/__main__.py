#!/usr/bin/env python3
"""Inspect the data policy.

    python3 -m jevpolicy                        show the policy
    python3 -m jevpolicy --class internal       who is cleared for a class
    python3 -m jevpolicy --check jevai.org internal
"""
import argparse
import sys

from .policy import (PolicyError, approved_targets, assert_service,
                     assert_target, load, rank)


def main():
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--class", dest="data_class", help="show clearance for a class")
    p.add_argument("--check", nargs=2, metavar=("ID", "CLASS"),
                   help="check one target or service against a class")
    p.add_argument("--redacted", action="store_true",
                   help="with --check, assert the text has been redacted")
    args = p.parse_args()

    try:
        pol = load()
    except PolicyError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if args.check:
        entry_id, cls = args.check
        is_service = any(s["id"] == entry_id for s in pol.get("services") or [])
        try:
            if is_service:
                assert_service(entry_id, cls, redacted=args.redacted, pol=pol)
            else:
                assert_target(entry_id, cls, pol=pol)
        except PolicyError as e:
            print(f"DENY  {e}")
            return 1
        print(f"ALLOW {entry_id} may handle {cls}")
        return 0

    if args.data_class:
        try:
            ok = approved_targets(args.data_class, pol)
        except PolicyError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 1
        print(f"targets cleared for {args.data_class!r}: {', '.join(ok) or 'none'}")
        return 0

    floor = pol.get("external_class_floor")
    print(f"classes: {' < '.join(pol['classes'])}")
    print(f"external floor: {floor}  "
          f"(at or above this, outbound text must be redacted)\n")
    for kind in ("targets", "services"):
        print(f"{kind}:")
        for e in pol.get(kind) or []:
            approved = e.get("approved_classes") or ["public"]
            top = max(approved, key=lambda c: rank(c, pol))
            by = e.get("approved_by") or "-"
            flag = "" if rank(top, pol) == 0 else f"  approved_by={by}"
            print(f"  {e['id']:<28} up to {top}{flag}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
