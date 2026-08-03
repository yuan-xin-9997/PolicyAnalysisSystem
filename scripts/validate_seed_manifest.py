"""Offline maintenance validation for the packaged Xinhua seed manifest."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from policy_analysis.sources.bootstrap import SeedManifestError, load_seed_manifest


def main(_argv: Sequence[str] | None = None) -> int:
    try:
        load_seed_manifest()
    except SeedManifestError:
        print("seed manifest invalid: 1 invalid, 0 duplicate", file=sys.stderr)
        return 1
    print("seed manifest valid: 0 invalid, 0 duplicate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
