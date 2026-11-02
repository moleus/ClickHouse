#!/usr/bin/env python3
"""Lab experiment: inline circular rate sliding-window queue with heap fallback.

Do NOT change the stored bucket state, serialized layout, merge logic, rate
extrapolation, sample deduplication or any other TimeSeries aggregator.
A bounded local ring stores the most recent 8 summaries during finalization.
Long windows migrate in chronological order to the existing tracked deque.
The default query implementation remains available as the A/B/A control.
"""
from pathlib import Path

p = Path("src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesExtrapolatedValue.h")
text = p.read_text()
anchor = "#include <cstddef>\n#include <cstring>"
replacement = "#include <array>\n#include <cstddef>\n#include <cstring>\n#include <memory>"
if text.count(anchor) != 1:
    raise RuntimeError(f"Unexpected include anchor: {text.count(anchor)}")
text = text.replace(anchor, replacement, 1)

anchor = """    /// Sliding aggregator for rate/increase/delta: preaggregates each bucket into a `Summary` and keeps the window's
    /// summary in a `SlidingSum`. `Summary` is invertible, so the window is maintained with a single running
    /// sum in O(1) per bucket; `getResult` reads its first/last sample, count and resets.
    struct Aggregator
    {
        AggregateFunctionTimeseriesSlidingSum<Summary> sliding_sum;
"""
replacement = """    /// Lab experiment: short windows can store their chronological summaries
    /// inline without a heap allocation. When a query needs more than eight
    /// overlapping buckets the full tracked deque is used. The combination and
    /// removal order stays identical to AggregateFunctionTimeseriesSlidingSum.
    struct InlineSlidingSum
    {
        static constexpr bool is_invertible = true;
        static constexpr size_t capacity = 8;
        using Entry = std::pair<GridScaleTimestampType, Summary>;

        std::array<Entry, capacity> local{};
        size_t head = 0;
        size_t count = 0;
        std::unique_ptr<DequeWithMemoryTracking<Entry>> spilled;
        Summary current_sum;

        void add(Summary && value, GridScaleTimestampType timestamp)
        {
            current_sum.merge(value);
            if (!spilled)
            {
                if (count < capacity)
                {
                    local[(head + count) % capacity] = {timestamp, std::move(value)};
                    ++count;
                    return;
                }

                spilled = std::make_unique<DequeWithMemoryTracking<Entry>>();
                for (size_t i = 0; i < count; ++i)
                    spilled->push_back(std::move(local[(head + i) % capacity]));
                head = 0;
                count = 0;
            }
            spilled->emplace_back(timestamp, std::move(value));
        }

        void removeBefore(GridScaleTimestampType cut_off)
        {
            if (!spilled)
            {
                while (count && local[head].first <= cut_off)
                {
                    Summary leaving = std::move(local[head].second);
                    head = (head + 1) % capacity;
                    --count;
                    current_sum.unmerge(leaving, count ? &local[head].second : nullptr);
                }
                return;
            }

            while (!spilled->empty() && spilled->front().first <= cut_off)
            {
                Summary leaving = std::move(spilled->front().second);
                spilled->pop_front();
                current_sum.unmerge(leaving, spilled->empty() ? nullptr : &spilled->front().second);
            }
        }

        const Summary & getCurrentSum() const { return current_sum; }
    };

    /// Sliding aggregator for rate/increase/delta: preaggregates each bucket
    /// into a Summary and keeps the running result in the inline queue above.
    struct Aggregator
    {
        InlineSlidingSum sliding_sum;
"""
if text.count(anchor) != 1:
    raise RuntimeError(f"Unexpected sliding sum anchor: {text.count(anchor)}")
text = text.replace(anchor, replacement, 1)
p.write_text(text)
print("LAB_RATE_INLINE_WINDOW_CPP_APPLIED", p)
