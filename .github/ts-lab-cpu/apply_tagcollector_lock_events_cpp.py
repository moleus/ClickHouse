#!/usr/bin/env python3
"""Lab C++ patch: exact TimeSeries per-query collector lock WAIT and HOLD.

Instrument both baseline and prehash binaries with the SAME counters.
Measure durations in nanoseconds to include short uncontended lock holds.
Counters are per-thread ProfileEvents propagated to the query log.
No change to map semantics, locking order, or exception behavior.
"""
from pathlib import Path

events=Path("src/Common/ProfileEvents.cpp")
s=events.read_text()
needle='    M(TimeSeriesTagsDeduplicationCacheMisses, "Number of rows written to the tags tables of TimeSeries tables after they were not found in the deduplication cache or the same insert had already written them.", ValueType::Number) \\'
# Locate anchor by event name to tolerate description modifications.
lines=s.splitlines(keepends=True)
positions=[i for i,line in enumerate(lines) if "M(TimeSeriesTagsDeduplicationCacheMisses," in line]
assert len(positions)==1,"Cannot locate pinned TimeSeries ProfileEvents"
added=[
'    M(TimeSeriesCollectorReadLockWaitNanoseconds, "Time waiting to acquire the TimeSeries tags collector shared lock during storeTagsTyped; accumulated over query worker threads.", ValueType::Nanoseconds) \\',
'    M(TimeSeriesCollectorReadLockHoldNanoseconds, "Time holding the TimeSeries tags collector shared lock during storeTagsTyped; accumulated over query worker threads.", ValueType::Nanoseconds) \\',
'    M(TimeSeriesCollectorWriteLockWaitNanoseconds, "Time waiting to acquire the TimeSeries tags collector exclusive lock during storeTagsTyped; accumulated over query worker threads.", ValueType::Nanoseconds) \\',
'    M(TimeSeriesCollectorWriteLockHoldNanoseconds, "Time holding the TimeSeries tags collector exclusive lock during storeTagsTyped; accumulated over query worker threads.", ValueType::Nanoseconds) \\',
'    M(TimeSeriesCollectorWriteAcquisitions, "Number of exclusive lock acquisitions during storeTagsTyped.", ValueType::Number) \\',
'    M(TimeSeriesCollectorPendingIDs, "Number of previously unseen TimeSeries IDs that are processed while holding the collector exclusive mutex.", ValueType::Number) \\',
]
lines[positions[0]+1:positions[0]+1]=[l+"\n" for l in added]
events.write_text("".join(lines))

path=Path("src/Interpreters/ContextTimeSeriesTagsCollector.cpp")
c=path.read_text()
assert c.count("#include <Common/PODArray.h>")==1
c=c.replace("#include <Common/PODArray.h>","#include <Common/PODArray.h>\n#include <Common/ProfileEvents.h>\n#include <chrono>")
# Built-in event names are not declared in ProfileEvents.h: users of new
# ProfileEvents must provide extern declarations in their own translation unit.
declarations = """namespace ProfileEvents
{
    extern const Event TimeSeriesCollectorReadLockWaitNanoseconds;
    extern const Event TimeSeriesCollectorReadLockHoldNanoseconds;
    extern const Event TimeSeriesCollectorWriteLockWaitNanoseconds;
    extern const Event TimeSeriesCollectorWriteLockHoldNanoseconds;
    extern const Event TimeSeriesCollectorWriteAcquisitions;
    extern const Event TimeSeriesCollectorPendingIDs;
}

"""
assert c.count("namespace DB\\n{")==1
c=c.replace("namespace DB\\n{",declarations+"namespace DB\\n{")

marker='    using Group = ContextTimeSeriesTagsCollector::Group;\n'
assert c.count(marker)==1
instrument="""    /// Laboratory-only lock timing. The hold timer destructor runs before
    /// the corresponding lock guard is destroyed, including when exceptions
    /// unwind. ProfileEvents increments add their own small measured overhead.
    using CollectorClock = std::chrono::steady_clock;
    UInt64 elapsedNanos(CollectorClock::time_point start)
    {
        return static_cast<UInt64>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            CollectorClock::now() - start).count());
    }
    struct CollectorLockHoldTimer
    {
        ProfileEvents::Event event;
        CollectorClock::time_point start = CollectorClock::now();
        explicit CollectorLockHoldTimer(ProfileEvents::Event event_) : event(event_) {}
        ~CollectorLockHoldTimer() noexcept
        {
            ProfileEvents::increment(event, elapsedNanos(start));
        }
    };
"""
c=c.replace(marker,marker+"\n"+instrument)
start=c.index("void ContextTimeSeriesTagsCollector::storeTagsTyped(")
stop=c.index("void ContextTimeSeriesTagsCollector::storeTagsGeneric(",start)
sub=c[start:stop]
read_before="""    {
        SharedLockGuard lock{mutex};
        /// The cast selects the const overload"""
read_after="""    {
        const auto lock_wait_start = CollectorClock::now();
        SharedLockGuard lock{mutex};
        ProfileEvents::increment(ProfileEvents::TimeSeriesCollectorReadLockWaitNanoseconds, elapsedNanos(lock_wait_start));
        CollectorLockHoldTimer lock_hold{ProfileEvents::TimeSeriesCollectorReadLockHoldNanoseconds};
        /// The cast selects the const overload"""
assert sub.count(read_before)==1
sub=sub.replace(read_before,read_after)
write_before="""    {
        std::lock_guard lock{mutex};
        auto & id_map = getTypedIDMap<IDType>().map;"""
write_after="""    {
        const auto lock_wait_start = CollectorClock::now();
        std::lock_guard lock{mutex};
        ProfileEvents::increment(ProfileEvents::TimeSeriesCollectorWriteLockWaitNanoseconds, elapsedNanos(lock_wait_start));
        ProfileEvents::increment(ProfileEvents::TimeSeriesCollectorWriteAcquisitions);
        ProfileEvents::increment(ProfileEvents::TimeSeriesCollectorPendingIDs, num_rows_to_store - num_found_groups);
        CollectorLockHoldTimer lock_hold{ProfileEvents::TimeSeriesCollectorWriteLockHoldNanoseconds};
        auto & id_map = getTypedIDMap<IDType>().map;"""
assert sub.count(write_before)==1
sub=sub.replace(write_before,write_after)
c=c[:start]+sub+c[stop:]
path.write_text(c)
print("TAGS_COLLECTOR_LOCK_PROFILE_EVENTS_PATCH_APPLIED",flush=True)
