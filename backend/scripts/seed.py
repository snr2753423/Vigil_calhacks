"""Seed Tiger with N days of synthetic history ending now (SPEC §8). Implemented in Phase 2."""

import argparse
import sys

from app.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=get_settings().seed_days)
    args = parser.parse_args()
    print(f"seed: stub — would seed {args.days} day(s) of history. Implemented in Phase 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
