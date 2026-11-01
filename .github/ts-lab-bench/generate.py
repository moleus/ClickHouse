#!/usr/bin/env python3
"""Generate identical deterministic counter series for ClickHouse and VictoriaMetrics."""
import argparse
import datetime as dt
import json
from pathlib import Path
import time

p = argparse.ArgumentParser()
p.add_argument("--outdir", default="tslab-data")
p.add_argument("--series", type=int, default=512)
p.add_argument("--points", type=int, default=720)
p.add_argument("--step", type=int, default=60)
p.add_argument("--start-epoch", type=int, default=0)
a = p.parse_args()
out = Path(a.outdir)
out.mkdir(parents=True, exist_ok=True)
base = a.start_epoch or (int(time.time()) // a.step) * a.step - a.points * a.step
with (out/"vm-import.prom").open("w") as vm, (out/"ch-import.jsonl").open("w") as ch:
    for i in range(a.series):
        labels = dict(host=f"h{i:04d}", region="east" if i % 2 == 0 else "west", team=f"t{i%32:02d}")
        labels_text = ",".join(f'{k}="{v}"' for k, v in labels.items())
        values = []
        value = float(1000 + i*50)
        for j in range(a.points):
            t = base + j*a.step
            value += 1 + (i*7+j*3)%9
            timestamp = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            values.append([timestamp, value])
            vm.write(f"bench_metric{{{labels_text}}} {value:.1f} {t*1000}\n")
        ch.write(json.dumps({"metric_name":"bench_metric","tags":labels,"samples":values},separators=(",",":"))+"\n")
manifest = dict(series=a.series, samples_per_series=a.points, samples_total=a.series*a.points,
                step_seconds=a.step, start_epoch=base, end_epoch=base+(a.points-1)*a.step,
                eval_start_epoch=base+(a.points//2)*a.step,
                eval_end_epoch=base+(a.points-1)*a.step, eval_step_seconds=a.step)
(out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
print(json.dumps(manifest,indent=2))
