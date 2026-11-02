# Overflow fallback arithmetic: proof and negative control before a C++ change

2026-11-02. This is a theoretical/algorithmic experiment, not a ClickHouse patch.

## Problem
Existing independent lab aggregate `avgKBNLab` reproduces the TimeSeries-native Kahan-Babuska-Neumaier compensated sum and count on tested finite inputs, but its final mean can be wrong if the running sum overflows Float64. Counterexample: `avg([1e308, 1e308]) = 1e308` (finite) while the existing lab aggregate returns +Inf (run 41446233352).

Prometheus `promql/engine.go` implements grouped AVG using compensated direct sum until a prospective running sum overflows, then switches to an **incremental mean**. See the AVG case in upstream `promql/engine.go` (2026-11-02 read, approximately lines 4444-4530). It also warns that earlier/later switching and summation order affect accuracy.

## Algebraic proof
For finite values `x_i`, `mu_n = sum(x_i,i=1..n)/n`. The recurrence
`mu_n = ((n-1)/n)*mu_(n-1) + x_n/n`
is exact in real arithmetic, because `((n-1)/n) * sum(first n-1)/(n-1) + x_n/n = sum(first n)/n`. A weighted merge of disjoint partial state means with counts A,B likewise satisfies
`mu_(A∪B) = (n_A/(n_A+n_B))*mu_A + (n_B/(n_A+n_B))*mu_B`.

For finite inputs bounded in magnitude by max finite Float64, the exact mean lies in their convex hull and is representable in magnitude, even when their unscaled sum cannot fit in Float64. But **floating-point evaluation of the recurrence and state merges is not exact or associative**. The algebra proves real-number identities, not robust numerical error bounds or bitwise parity to Prometheus.

## Falsification experiment contract
Implement a standalone Python model of:
1. standard Neumaier direct sum while the new running sum is finite;
2. weighted incremental compensated mean after prospective overflow;
3. parallel-like merges of partial states using weighted means;
4. high-precision Decimal.from_float oracle for IEEE754 input values.

Cases: [1e308]*2, [1e308]*99, [1e308]*100+[-1e308]*100, same plus [1e100]*5, ordinary finite cancellation [1e18,1,-1e18]. Report raw result and both absolute error and error scaled by maximum sample magnitude. `math.fsum` can overflow on [1e308]*2; use Decimal with precision >=1000 to construct the oracle instead.

**Hypothesis**: incremental fallback fixes the simple finite-mean overflow cases, but can lose small residuals after huge positive-negative cancellation. This is a negative control: if true, do not claim the proposed fallback is a general accuracy solution merely because it avoids Inf.

No C++ compile or production changes until actual cost, special value and merge-fidelity conditions are defined.
