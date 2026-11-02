# Streaming KBN overhead on 400k short-lived Jenkins-like stage series

2026-11-02. This is a proof-first lab test. It does not modify normal PromQL or build a new C++ binary.

## Facts and question
- Lab C++ aggregate `avgKBNLab(Float64)` passed 7 cross-series finite cancellation cases and explicit State/Merge for 1/4/8 threads (41446233352, 41447468849), but only on tiny test data.
- Baseline C++ v4 one-point candidate default-OFF, built at 41447045583, uses ordinary final `avg(series_avg)` and reduces latency/memory on 400k one-sample short-lived IDs (41455684534).
- Question: how much CPU/memory does an opt-in stage KBN cost on **400k** per-ID values with 1,520 stage/day groups, not seven tiny groups?

## Experiment and oracle
- Load same source fixture as 41455684534: 40 days ×10,000 new distinct IDs/day, one Float64 sample/ID, stage i%38, value computed as `(i%1900+1)*0.1 + day*0.001`. The exact JSON input strings are hashed; no extra mutation during measurement.
- Use the already compiled v4 O1 binary (same pinned source and KBN function), but call SQL directly through the standard `timeSeriesSelector` with identical per-ID `timeSeriesAvgToGrid`. Compare SQL stage `avg(series_avg)` vs `avgKBNLab(series_avg)`, then ordinary avg again. No engine source change, no dependence on C++ converter opt-in selection.
- At 14d and 40d, 2 warmups+5 measured executions per phase and `max_threads=4`. Compare all 38×E stage keys/timestamps; finite outputs must match within 1e-10 relative /1e-8 absolute. Independently derive exact per-stage/day means using Python `math.fsum` from the precise Float64 values that the source fixture produces.
- Record p50/p95 query duration, tracked memory, read_rows and per-stage max absolute error vs mathematical oracle, along with raw outputs and per-phase query IDs.

## Acceptance
- If KBN fixes numerical errors but adds less than 10% time and memory to the 400k stage cost, it remains viable for a separate opt-in numerical-quality path; if overhead is larger, do not combine it with fast path until justified by real input error incidence.
- Even a success does **not** justify replacing ordinary PromQL AVG generally. The KBN lab aggregate still overflows for `avg(1e308,1e308)`; the incremental-mean fallback can lose small residues (41454531901).
- Same 400k dataset is roughly 1/9.5 of planned 40-day production series cardinality. O1 benchmark only; no production or master writes, no workflow schedules/cancellations.
