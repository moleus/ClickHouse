#!/usr/bin/env python3
"""Generate the canonical 95k/day Jenkins TimeSeries fixture without requiring CH.

This reuses the exact JSON row algorithm from
.github/ts-lab-bench/generate_95k_daily_churn_ts.py and compares its output
SHA256 against the frozen 1,330,000-row source reference.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path

DAYS = 14
PER_DAY = 95000
METRIC = "jenkins_ci_95k_daily"
SHA = "8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"


def generate(path, manifest):
    base = int(datetime.datetime(2026, 8, 4, tzinfo=datetime.timezone.utc).timestamp())
    end = base + DAYS * 86400
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with dest.open("wb") as out:
        for day in range(DAYS):
            for i in range(PER_DAY):
                sample_time = base + day * 86400 + 3600 + (i * 1049) % 76000
                timestamp = datetime.datetime.fromtimestamp(
                    sample_time, datetime.timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S.000")
                branch = "main" if i % 7 == 0 else f"feature-{i%19}"
                row = {
                    "metric_name": METRIC,
                    "tags": {
                        "build": f"day-{day:03d}-build-{i:05d}",
                        "stage": f"stage-{i%38:04d}",
                        "branch": branch,
                        "project": f"project-{i%8:02d}",
                    },
                    "samples": [[timestamp, (i%1900+1)*.1+day*.001]],
                }
                encoded = (json.dumps(row, separators=(",", ":")) + "\n").encode()
                out.write(encoded)
                h.update(encoded)
    digest = h.hexdigest()
    if digest != SHA:
        raise AssertionError(("canonical Jenkins SHA mismatch", digest, SHA))
    meta = {
        "base": base, "end": end, "days": DAYS, "per_day": PER_DAY,
        "series": DAYS * PER_DAY, "samples": DAYS * PER_DAY,
        "sha256": digest, "fixture_bytes": dest.stat().st_size,
        "generator_source": ".github/ts-lab-bench/generate_95k_daily_churn_ts.py",
        "metric": METRIC,
    }
    Path(manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return meta


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", required=True)
    p.add_argument("--manifest", required=True)
    a = p.parse_args()
    result = generate(a.fixture, a.manifest)
    print("R27_GENERATED_CANONICAL", json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
