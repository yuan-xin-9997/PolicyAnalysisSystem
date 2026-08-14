"""Offline maintenance validation for every packaged Xinhua seed manifest."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from policy_analysis.sources.bootstrap import (
    DEFAULT_SCENARIOS,
    SeedManifestError,
    load_seed_manifest,
)


def main(_argv: Sequence[str] | None = None) -> int:
    invalid = 0
    for spec in DEFAULT_SCENARIOS:
        try:
            load_seed_manifest(spec=spec)
        except SeedManifestError:
            invalid += 1
            print(
                f"seed manifest invalid ({spec.key}): 1 invalid, 0 duplicate",
                file=sys.stderr,
            )
    if invalid:
        print(f"seed manifest valid: {len(DEFAULT_SCENARIOS) - invalid} invalid, 0 duplicate")
        return 1
    print("seed manifest valid: 0 invalid, 0 duplicate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
