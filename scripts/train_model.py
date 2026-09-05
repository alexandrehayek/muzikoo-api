#!/usr/bin/env python
"""Fit the KNN recommender on the tracks table and save the artifact.

    python scripts/train_model.py
    python scripts/train_model.py --out models/knn_model.joblib
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muzikoo import recommender
from muzikoo.config import get_settings


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=settings.model_path)
    parser.add_argument("--n-neighbors", type=int, default=5, help="model default; per-query limit overrides it")
    args = parser.parse_args()

    print("fetching features from the database ...", file=sys.stderr)
    started = time.perf_counter()
    try:
        artifact = recommender.fit(n_neighbors=args.n_neighbors)
    except Exception as exc:  # noqa: BLE001
        print(f"training failed: {exc}", file=sys.stderr)
        return 1

    path = recommender.save(artifact, args.out)
    size_mb = path.stat().st_size / 1e6
    print(
        f"fitted on {artifact.n_rows:,} tracks x {len(artifact.feature_columns)} features "
        f"in {time.perf_counter() - started:.1f}s",
        file=sys.stderr,
    )
    print(f"saved {path} ({size_mb:.1f} MB), sklearn {artifact.sklearn_version}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
