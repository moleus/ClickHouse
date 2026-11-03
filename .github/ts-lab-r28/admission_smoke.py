#!/usr/bin/env python3
"""R28: cheap source-pinned ClickHouse QUERY-slot admission smoke.

No TimeSeries performance claim. This validates queueing, admission
timeout and exact responses before a high-cardinality native benchmark.
"""
import concurrent.futures
import json
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = "http://127.0.0.1:18191"


def sql(statement, timeout=30):
    req = urllib.request.Request(
        HOST + "/?wait_end_of_query=1",
        data=statement.encode(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode().strip()


def request(statement, start_barrier=None):
    if start_barrier:
        start_barrier.wait(timeout=10)
    begin = time.monotonic()
    try:
        body = sql(statement)
        return {
            "ok": True,
            "body": body,
            "elapsed_ms": round(1000 * (time.monotonic() - begin), 2),
        }
    except urllib.error.HTTPError as ex:
        return {
            "ok": False,
            "body": ex.read().decode(errors="replace")[:1500],
            "elapsed_ms": round(1000 * (time.monotonic() - begin), 2),
        }


def run_wave(slots, clients=8):
    sql("CREATE OR REPLACE WORKLOAD all SETTINGS "
        f"max_concurrent_queries={slots},max_waiting_queries=16")
    barrier = threading.Barrier(clients + 1)
    query = ("SELECT sleepEachRow(0.2) FROM numbers(1) "
             "SETTINGS workload='all',workload_admission_timeout_ms=10000 "
             "FORMAT TSV")
    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [executor.submit(request, query, barrier) for _ in range(clients)]
        t0 = time.monotonic()
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
        wall_ms = 1000 * (time.monotonic() - t0)
    assert all(x["ok"] and x["body"] == "0" for x in results), results
    return {
        "slots": slots, "clients": clients, "wall_ms": round(wall_ms, 2),
        "request_p50_ms": round(statistics.median(x["elapsed_ms"] for x in results), 2),
        "request_max_ms": max(x["elapsed_ms"] for x in results),
        "results": results,
    }


def check_timeout():
    sql("CREATE OR REPLACE WORKLOAD all SETTINGS "
        "max_concurrent_queries=1,max_waiting_queries=16")
    holder_query = ("SELECT sleepEachRow(2) FROM numbers(1) "
                    "SETTINGS workload='all',workload_admission_timeout_ms=10000 FORMAT TSV")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(request, holder_query)
        # Confirm the holder is executing, not merely waiting for HTTP dispatch.
        found = False
        for _ in range(60):
            n = int(sql("SELECT count() FROM system.processes "
                        "WHERE query LIKE '%sleepEachRow(2)%' AND "
                        "query NOT LIKE '%system.processes%' FORMAT TSV"))
            if n:
                found = True
                break
            time.sleep(0.025)
        assert found, "Holder did not enter execution"
        waiter = request("SELECT 42 SETTINGS workload='all',"
                         "workload_admission_timeout_ms=100 FORMAT TSV")
        holder = future.result(timeout=10)
    assert holder["ok"] and holder["body"] == "0", holder
    assert not waiter["ok"] and "QUERY_SLOT_ACQUISITION_TIMEOUT" in waiter["body"], waiter
    return {"holder": holder, "waiter": waiter}


def main():
    assert sql("SELECT 1") == "1"
    sql("CREATE RESOURCE IF NOT EXISTS query (QUERY)")
    sql("CREATE WORKLOAD IF NOT EXISTS all SETTINGS "
        "max_concurrent_queries=8,max_waiting_queries=16")
    try:
        phase = [run_wave(8),run_wave(2),run_wave(2),run_wave(8)]
        # A queueing control, not a claim of faster service.
        assert phase[1]["wall_ms"] > phase[0]["wall_ms"] * 1.3, phase
        timeout = check_timeout()
        result = {
            "question_id": "R28",
            "scientific_decision": "FUNCTIONAL_SMOKE_ONLY",
            "passed": True,
            "phases": phase,
            "timeout": timeout,
            "limit": "sleep-based admission semantics only; no native PromQL or RSS proof",
        }
    finally:
        sql("DROP WORKLOAD IF EXISTS all")
        sql("DROP RESOURCE IF EXISTS query")
    print("R28_RESULT " + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
