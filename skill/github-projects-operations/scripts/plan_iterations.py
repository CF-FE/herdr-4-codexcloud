#!/usr/bin/env python3
"""Print an explicit iteration schedule; no network, credentials, or file writes."""

import argparse
from datetime import date, timedelta
import json
import re


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="First date, YYYY-MM-DD")
    parser.add_argument("--days", required=True, type=int, help="Days in each iteration")
    parser.add_argument("--count", required=True, type=int, help="Number of iterations")
    parser.add_argument("--title-prefix", required=True, help="Prefix for numbered titles")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", args.start):
        parser.error("--start must use YYYY-MM-DD")
    if args.days <= 0 or args.count <= 0:
        parser.error("--days and --count must be positive integers")
    title_prefix = args.title_prefix.strip()
    if not title_prefix:
        parser.error("--title-prefix must not be empty")
    try:
        start = date.fromisoformat(args.start)
        # Validate the complete span before allocating a schedule or printing JSON.
        start + timedelta(days=args.days * args.count - 1)
        iterations = [
            {"title": f"{title_prefix} {index + 1}",
             "start_date": (start + timedelta(days=index * args.days)).isoformat(),
             "duration": args.days}
            for index in range(args.count)
        ]
    except (ValueError, OverflowError) as exc:
        parser.error(f"invalid calendar schedule: {exc}")
    print(json.dumps({"start_date": start.isoformat(),
                      "iteration_duration": args.days,
                      "iterations": iterations}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
