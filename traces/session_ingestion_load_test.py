"""create N langfuse sessions and ingest events under each one.

langfuse creates sessions implicitly — there is no separate session-create endpoint.
a session appears in langfuse as soon as any trace with that sessionId is ingested.

for each session:
  POST /api/public/ingestion — sends exactly --events-per-session events under a fresh sessionId.

example: --sessions 67 --events-per-session 3
  → 67 ingestion calls = 201 events across 67 unique sessionIds.

usage:
    python session_ingestion_load_test.py --sessions 67 --events-per-session 3
"""

import argparse
import asyncio
import base64
import copy
import json
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()


OBS_TYPE_MAP = {
    "GENERATION": "generation-create",
    "SPAN":       "span-create",
    "EVENT":      "event-create",
}


class Metrics:
    def __init__(self):
        self.status_counts = Counter()
        self.latencies = []
        self.exceptions = []

    def record_success(self, status_code: int, latency: float):
        self.status_counts[status_code] += 1
        self.latencies.append(latency)

    def record_exception(self, exc: Exception):
        self.status_counts["exception"] += 1
        if len(self.exceptions) < 10:
            self.exceptions.append(exc)


def norm_ts(ts):
    return str(ts).replace(" ", "T") if ts else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_template(path: str) -> list[dict]:
    """load trace.json and flatten into a list of ingestion events."""
    with open(path) as f:
        traces = json.load(f)

    events = []

    for t in traces:
        events.append({
            "_kind": "trace",
            "_orig_trace_id": t["id"],
            "type": "trace-create",
            "body": {k: v for k, v in {
                "id":        t["id"],
                "timestamp": norm_ts(t.get("timestamp")),
                "name":      t.get("name"),
                "input":     t.get("input"),
                "output":    t.get("output"),
                "userId":    t.get("userId"),
                "metadata":  t.get("metadata"),
                "tags":      t.get("tags"),
                "release":   t.get("release"),
                "version":   t.get("version"),
            }.items() if v is not None},
        })

        for obs in t.get("observations", []):
            obs_type = obs.get("type", "SPAN")
            events.append({
                "_kind": "obs",
                "_orig_id": obs["id"],
                "_orig_trace_id": t["id"],
                "_orig_parent_id": obs.get("parentObservationId"),
                "type": OBS_TYPE_MAP.get(obs_type, "span-create"),
                "body": {k: v for k, v in {
                    "id":                  obs["id"],
                    "traceId":             t["id"],
                    "name":                obs.get("name"),
                    "startTime":           norm_ts(obs.get("startTime")),
                    "endTime":             norm_ts(obs.get("endTime")),
                    "input":               obs.get("input"),
                    "output":              obs.get("output"),
                    "metadata":            obs.get("metadata"),
                    "level":               obs.get("level"),
                    "parentObservationId": obs.get("parentObservationId"),
                    "model":               obs.get("model"),
                    "modelParameters":     obs.get("modelParameters"),
                    "usage":               obs.get("usage"),
                }.items() if v is not None},
            })

    return events


def freshen_session(template: list[dict], session_id: str) -> list[dict]:
    """assign fresh UUIDs to all trace/obs IDs and inject session_id into
    every trace body. returns a flat list of ingestion-ready event dicts."""
    trace_id_map = {}
    obs_id_map = {}

    for e in template:
        if e["_kind"] == "trace":
            trace_id_map.setdefault(e["_orig_trace_id"], str(uuid.uuid4()))
        else:
            obs_id_map.setdefault(e["_orig_id"], str(uuid.uuid4()))
            trace_id_map.setdefault(e["_orig_trace_id"], str(uuid.uuid4()))

    ts = now_iso()
    out = []

    for e in template:
        body = copy.deepcopy(e["body"])

        if e["_kind"] == "trace":
            body["id"] = trace_id_map[e["_orig_trace_id"]]
            body["sessionId"] = session_id
        else:
            body["id"] = obs_id_map[e["_orig_id"]]
            body["traceId"] = trace_id_map[e["_orig_trace_id"]]
            parent = e["_orig_parent_id"]
            if parent:
                body["parentObservationId"] = obs_id_map.get(
                    parent, str(uuid.uuid4())
                )

        out.append({
            "id": str(uuid.uuid4()),
            "timestamp": ts,
            "type": e["type"],
            "body": body,
        })

    return out


async def run_session(
    client: httpx.AsyncClient,
    host: str,
    auth: str,
    template: list[dict],
    events_per_session: int,
    metrics: Metrics,
    sem: asyncio.Semaphore,
):
    session_id = str(uuid.uuid4())
    events = freshen_session(template[:events_per_session], session_id)
    payload = {"batch": events}  # exactly events_per_session events, one POST

    async with sem:
        started = time.perf_counter()
        try:
            response = await client.post(
                f"{host}/api/public/ingestion",
                json=payload,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json",
                },
            )
            metrics.record_success(response.status_code, time.perf_counter() - started)
        except Exception as exc:
            metrics.record_exception(exc)


def percentile(sorted_values, p):
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * p))
    return sorted_values[idx]


async def main(
    sessions: int,
    events_per_session: int,
    trace_file: str,
    concurrency: int,
):
    host = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

    template = load_template(trace_file)
    if events_per_session > len(template):
        events_per_session = len(template)

    print(f"sessions:            {sessions}")
    print(f"events per session:  {events_per_session}")
    print(f"total events:        {sessions * events_per_session}")
    print(f"total requests:      {sessions}  (1 ingestion POST per session)")
    print(f"concurrency:         {concurrency}  (max in-flight at once)")
    print()

    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
    metrics = Metrics()
    sem = asyncio.Semaphore(concurrency)

    print(f"creating {sessions} sessions and ingesting {events_per_session} events each ...")
    started = time.perf_counter()

    async with httpx.AsyncClient(limits=limits, timeout=timeout, http2=False) as client:
        await asyncio.gather(*[
            run_session(
                client=client,
                host=host,
                auth=auth,
                template=template,
                events_per_session=events_per_session,
                metrics=metrics,
                sem=sem,
            )
            for _ in range(sessions)
        ])

    elapsed = time.perf_counter() - started

    total_completed = sum(metrics.status_counts.values())
    errors = sum(
        count
        for code, count in metrics.status_counts.items()
        if code == "exception" or (isinstance(code, int) and code >= 400)
    )
    successes = total_completed - errors

    print()
    print(f"done in {elapsed:.2f}s")
    print(f"request throughput:  {total_completed / elapsed:.2f} req/s")
    print(f"event throughput:    {(sessions * events_per_session) / elapsed:.2f} events/s")
    print(f"success={successes}  errors={errors}")
    print()

    for code, count in sorted(metrics.status_counts.items(), key=lambda x: str(x[0])):
        print(f"  {code}: {count}")

    if metrics.latencies:
        metrics.latencies.sort()
        avg = sum(metrics.latencies) / len(metrics.latencies)
        print()
        print(f"avg={avg*1000:.1f}ms")
        print(f"p50={percentile(metrics.latencies, 0.50)*1000:.1f}ms")
        print(f"p90={percentile(metrics.latencies, 0.90)*1000:.1f}ms")
        print(f"p95={percentile(metrics.latencies, 0.95)*1000:.1f}ms")
        print(f"p99={percentile(metrics.latencies, 0.99)*1000:.1f}ms")
        print(f"max={metrics.latencies[-1]*1000:.1f}ms")

    if metrics.exceptions:
        print()
        print("sample exceptions:")
        for exc in metrics.exceptions[:5]:
            print(f"  {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions",
        type=int,
        default=50,
        help="number of concurrent interview sessions to simulate",
    )
    parser.add_argument(
        "--events-per-session",
        type=int,
        default=3,
        help="number of events in the single ingestion POST per session",
    )
    parser.add_argument(
        "--trace-file",
        default="traces/interview/trace.json",
        help="langfuse-native trace file to use as event source",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=100,
        help="max number of in-flight HTTP requests at once (default: 100)",
    )

    args = parser.parse_args()

    asyncio.run(
        main(
            sessions=args.sessions,
            events_per_session=args.events_per_session,
            trace_file=args.trace_file,
            concurrency=args.concurrency,
        )
    )
