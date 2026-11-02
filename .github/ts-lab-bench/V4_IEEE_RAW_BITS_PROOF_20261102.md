# Raw IEEE-754 output verification after JSON NULL ambiguity

2026-11-02. Pinned base ClickHouse commit 52529f0c711566d696917678bf15e7068772fdde.

## Observation, not a proof of numerical parity
Run 41452375304 SUCCESS, 13 input stages (quiet NaN, stale NaN, infinities, duplicate qNaN/finite timestamp, ±0, 1ms boundaries). Original / v4-on / v4-off / original returned the same 11 output point keys, the same JSON values, and correct strict-open-left/closed-right daily windows. But `JSONEachRow` **serializes all NaN and infinities as JSON null**. The prior test counted four nulls; it did NOT prove that both engines preserved the same IEEE-754 classes or signs.

## Source-level solution
The `prometheusQueryRange` table function returns `Array(Tuple(DateTime64(3), Float64))` prior to output serialization. Project `arrayMap(x -> tuple(toUnixTimestamp64Milli(tupleElement(x,1)), reinterpretAsUInt64(tupleElement(x,2))), samples)` in SQL and return the raw UInt64 patterns as integer or quoted decimal. This distinguishes quiet NaN, stale NaN, +Inf, -Inf, ±0 without lossy JSON. It does not change the aggregate calculation, only its final representation.

IEEE rules: exponent bits [62..52] all 1 and fraction nonzero -> NaN, fraction zero -> Inf with sign bit; exponent zero and fraction zero -> ±0 with sign; all other values finite. Dedicated stale marker `0x7ff0000000000002` must not appear in final output.

## Acceptance
Use the same 13-stage input generator as run 41452375304, unchanged pinned original and compiled v4. Four phases original/v4_on/v4_off/original, 2 warmups + 5 measured per phase, compare exact label/timestamp keys, classify IEEE bits using integer masking, require same classification, Inf sign, finite value bits and zero sign, report any NaN payload-bit differences separately. Assert expected stage output classes: qNaN+5→NaN; stale alone→absent; stale+7→7; +Inf+1→+Inf; -Inf-1→-Inf; both Inf→NaN; conflicting qNaN/5 same timestamp plus 7→finite mean 6. All 1ms day boundary checks remain.

Unexpected NaN payload differences may be unspecified by PromQL, so report them without premature failure if both are ordinary NaNs. Unexpected finite/Inf/zero sign, output keys, stale-marker leakage or different NaN-vs-Inf class is a correctness blocker.

This is a **separate** proof from general Prometheus semantics (especially overflow), O2 optimization flags and authorization. Lab only. No upstream/master changes.
