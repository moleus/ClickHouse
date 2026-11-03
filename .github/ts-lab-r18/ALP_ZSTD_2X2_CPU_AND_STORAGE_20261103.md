# R18: ALP transform and ZSTD level isolated in a 2×2 factorial

**Valid run:** [#41635457586](https://github.com/moleus/ClickHouse/actions/runs/41635457586), both jobs **SUCCESS**.
[Exact immutable evidence](../ts-lab/evidence/R18-alp-zstd-level-factorial-41635457586.json),
[preregistered hypothesis](VALUE_CODEC_2X2_FACTORIAL_PLAN_20261103.md),
[benchmark source](bench_value_codec_2x2.py),
[offline unit tests](test_value_codec_2x2_preflight.py).

## Why this experiment was necessary

Earlier R18 compared `CODEC(ALP,ZSTD(3))` against
`CODEC(ZSTD(1))`. It changed **the ALP transform and ZSTD level
simultaneously**. Its SAMPLES storage and latency differences are
real, but cannot distinguish the effects of these two operations.

On pinned ClickHouse source commit
`52529f0c711566d696917678bf15e7068772fdde`, the default
TimeSeries v11 SAMPLES subcolumn value codec is
`ALP,ZSTD(3)`, and `SAMPLES INNER COLUMNS` accepts subcolumn CODEC
overrides. This test makes the **same explicit SAMPLES schema**
for four alternatives:

| Physical label | Actual effective `samples.value` codec | ALP | ZSTD level |
|---|---|---|---|
| `alp_z3` | `ALP,ZSTD(3)` | yes | 3 |
| `alp_z1` | `ALP,ZSTD(1)` | yes | 1 |
| `raw_z3` | `ZSTD(3)` | no | 3 |
| `raw_z1` | `ZSTD(1)` | no | 1 |

All four were verified against actual `SHOW CREATE` DDL,
including unchanged `samples.timestamp CODEC(Delta(8),T64,ZSTD(3))`.
Full inner DDL, after replacing only table UUID/name and the value
codec token, is identical. RAW UUID ID, Float64, SAMPLES bucket=3600s,
hash tag index and all other settings are held fixed.

Two deterministic 3-day/400-node fixtures with **28,896 series and
2,488,320 original points each**:
- Patterned repeating gauges SHA256
  `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`.
- Independent high-entropy finite Float64 values SHA256
  `4b81b190cbcff7af58682e8c023cebfad22e3bb98afb591d8b2d9928eade9d57`.
  Metric names, labels and timestamps match the first fixture.

Each fixture has an **independent** four-logical-CPU AMD EPYC 9V74
GitHub runner. Each uses the same SHA-verified stock LLVM22.1.8
**O1 glibc** ClickHouse executable
`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`
from [#41596421285](https://github.com/moleus/ClickHouse/actions/runs/41596421285).
Do not pool *absolute* CPU or latency across the two runners.

For each fixture, three full native PromQL selectors
`pod_namespace`, `node_broad` and `burst15`, with four
independent paired experiments:
`alp_z3/raw_z3/raw_z3/alp_z3`,
`alp_z1/raw_z1/raw_z1/alp_z1`,
`alp_z3/alp_z1/alp_z1/alp_z3`,
`raw_z3/raw_z1/raw_z1/raw_z3`.
Each phase performs 2 warmups +16 measured requests.
**1,728 full native requests overall; 24/24 paired controls PASS**;
all nonempty native label/timestamp/numeric results match within
each fixture, all ID counts and 2,488,320 sample-element counts match,
all inner parts/marks immutable, no OOM. QueryFinish CPU and p50/p95
recorded for every trial. The full reports retain CPU trace and
processor samples from one query per arm.

## 1. Source-isolated physical storage effects

Compressed *SAMPLES* table bytes after one-part `OPTIMIZE FINAL`:

| Value codec | Patterned | High entropy |
|---|---:|---:|
| ALP + ZSTD(3) | 5,303,391 | **19,686,139** |
| ALP + ZSTD(1) | 5,550,516 | **19,686,139** |
| ZSTD(3) without ALP | **3,355,724** | 20,216,284 |
| ZSTD(1) without ALP | 3,414,486 | 20,216,284 |

Exact *one-variable* contrasts:

| Fixed condition / change | Patterned SAMPLES | High-entropy SAMPLES |
|---|---:|---:|
| Remove ALP, **hold ZSTD level 3** | **−36.72%** | **+2.69%** |
| Remove ALP, **hold ZSTD level 1** | −38.48% | +2.69% |
| ZSTD 3→1, **hold ALP on** | +4.66% | 0.00% |
| ZSTD 3→1, **hold ALP off** | +1.75% | 0.00% |

The storage reversal **is caused by the ALP transform's presence
under these controlled conditions**, not by switching ZSTD level
from 3 to 1. This is stronger than the original two-variable
measurement. High-entropy compressed bytes are identical across
ZSTD levels on this particular deterministic Float64 corpus; do not
infer the compressor always produces identical sizes at level1/3.

## 2. Native CPU: effects are selector-dependent

The native `QueryFinish.OSCPUVirtualTimeMicroseconds` per request
below holds ZSTD **level 3 fixed**, changing only ALP.

| Distribution + selector | ALP CPU | No ALP CPU | Change | A/A CPU drift |
|---|---:|---:|---:|---:|
| Patterned, `pod_namespace` | 54.633ms | 54.681ms | +0.09% | 7.08% |
| Patterned, `node_broad` | 179.223ms | 177.697ms | −0.85% | 1.55% |
| Patterned, `burst15` | 39.686ms | 39.791ms | +0.26% | 7.32% |
| Entropy, `pod_namespace` | 111.914ms | **102.548ms** | **−8.37%** | 0.74% |
| Entropy, `node_broad` | 419.601ms | 411.240ms | −1.99% | 0.90% |
| Entropy, `burst15` | 74.499ms | **66.506ms** | **−10.73%** | 4.90% |

For high-entropy `pod_namespace`, p50 decreases **78.232→74.097ms**
(−5.28%, baseline p50 drift0.43%) alongside the 8.37% CPU
improvement. For high-entropy `burst15`, p50 **55.117→50.840ms**
(−7.76%, drift3.03%). These are credible local gains with
full-query semantic parity. The broad-node ~2% CPU difference is
smaller and cannot justify a global default change.

**Correction to interpretation of earlier R18:** the original
two-variable comparison recorded `pod_namespace` CPU **+10%** for
ZSTD(1) against ALP+ZSTD(3) on one runner, but the new
fixed-level3 factorial gives effectively **0%** change on its
patterned fixture. The earlier 10% observation is valid for that
specific run and pair, **not a reproducible isolated ALP cost**.
It must not be used as a general per-codec latency rule.

Changing ZSTD level at fixed ALP status generally yielded small
CPU effects comparable with control drift. For high entropy the
major CPU win comes from removing ALP while holding ZSTD level
3 constant; changing level3→1 does not reproduce the entire gain.

## 3. CPU stack evidence and bounded attribution

The detailed full artifact includes representative (single-query)
`trace_log`, folded CPU stacks and summed
`processors_profile_log` work. On the entropy/namespace
**ALP+ZSTD3** representative query, **18 of110 CPU trace samples**
have `CompressionCodecALP` in their stack; for the paired
**ZSTD3-only** query, **0 of102**. On `burst15`, ALP **10/84**
versus ZSTD3-only **0/66**. The CPU category
`05_read_decode` in the same namespace pair falls **57→45**
samples; burst15 **43→24**.

These samples support a real ALP decoder cost, but they are not
a matched median measurement of time inside one function. The
raw-ZSTD pipeline still has decoder/transposition/memset work.
Do **not** sum overlapping folded stack categories, treat
sample counts as exact CPU ms, or attribute all measured CPU
difference to a particular ALP function without an instrumented
no-probe control.

## 4. Negative method runs and safe test coverage

Two earlier attempts in the **same R18 question** are preserved
in [method ledger M43](../ts-lab/METHODOLOGY_AND_FAILURES.md):

- [#41634612567](https://github.com/moleus/ClickHouse/actions/runs/41634612567)
  failed a wrong Python literal backtick check on correct DDL,
  before ingest. No compression or speed outcome.
- [#41634883798](https://github.com/moleus/ClickHouse/actions/runs/41634883798)
  verified all four effective DDL and physical storage bytes for
  both fixtures but failed before native queries: the imported
  selector dict was overwritten by a tuple of default case names.
  Physical bytes remain evidence; no read performance from this CI.

The corrected script uses `chr(96)` for actual backticks and
`CASES_DEFAULT` distinct from the R01 `CASES` dictionary.
Five new **pure unit tests** check selector mapping, 2×2 factor
coverage, actual quoted codec DDL and invalid codec/timestamp
rejection *before* downloading the binary or generating fixtures.
All unit tests PASS in both successful jobs. This is why the
final run is accepted and earlier ones are not.

Artifacts (full JSON, SHA verified):
- Patterned job: [artifact 13229074259](https://github.com/moleus/ClickHouse/actions/runs/41635457586/artifacts/13229074259),
  ZIP SHA256 `e20fcb428422cff32024512dd23ee45ae627b0297d5aee90293825ebdf3789fa`.
- Entropy job: [artifact 13229909479](https://github.com/moleus/ClickHouse/actions/runs/41635457586/artifacts/13229909479),
  ZIP SHA256 `6e4249a82852a528695a7baa314024a20715dd5957fb24fe05d65261c8fab9ae`.

## Decision

**The ALP transform is a genuine CPU/space tradeoff, and the best choice
depends on value entropy and query shape. Keep the stock default.**
For repetitive low-range values, removing ALP can reduce compressed
SAMPLES bytes by 36.7% without a reliable one-client CPU gain.
For high-entropy values, keeping ALP saves 2.7% of compressed storage,
but removing it saves ~8–11% of QueryFinish CPU for two selective
query shapes in the source-controlled same-runner tests.

The separate [5k-request start-alignment × 1/4/8-client R18](START_STAGGER_CODEC_CONCURRENCY_20261103.md)
showed that changing timestamps alone can increase broad-read CPU
~62%, outweighing the tested codec CPU effects. Do not combine
absolute timings across its different runner and this factorial.

Production promotion still requires a realistic mixed entropy
histogram, staggered start distribution, late data/counter/NaN
semantic oracle, natural merge/write amplification and the
**same optimized allocator/build** at 1/4/8 concurrent clients.
No full C++ recompilation, production deploy or global default
mutation was done in R18.
