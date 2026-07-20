"""load test for langfuse trace ingestion endpoint using real interview data.

mirrors prompts/http_load_test.py — same cli, same metrics. builds each POST
payload from traces/interview/trace.json (the real langfuse-format interview
trace used by trace.py) and freshens all ids per request so every POST
creates a brand-new trace on the server.

usage:
    python http_ingestion_load_test.py --total 2000 --concurrency 16 --batch-size 50
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
    """load trace.json and flatten it into a list of ingestion events.

    each entry carries the original trace_id / obs_id / parent_id so we can
    remap them consistently on every request.
    """
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
                "sessionId": t.get("sessionId"),
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


def build_request_batch(template: list[dict], batch_size: int) -> dict:
    """slice `batch_size` events off the template and freshen all ids so each
    POST creates a brand-new trace tree on the server.
    """
    slice_ = template[:batch_size]

    trace_id_map = {}
    obs_id_map = {}

    for e in slice_:
        if e["_kind"] == "trace":
            trace_id_map.setdefault(
                e["_orig_trace_id"],
                str(uuid.uuid4()),
            )
        else:
            obs_id_map.setdefault(
                e["_orig_id"],
                str(uuid.uuid4()),
            )
            trace_id_map.setdefault(
                e["_orig_trace_id"],
                str(uuid.uuid4()),
            )

    ts = now_iso()
    out = []

    for e in slice_:
        body = copy.deepcopy(e["body"])

        if e["_kind"] == "trace":
            body["id"] = trace_id_map[e["_orig_trace_id"]]
        else:
            body["id"] = obs_id_map[e["_orig_id"]]
            body["traceId"] = trace_id_map[e["_orig_trace_id"]]

            parent = e["_orig_parent_id"]
            if parent:
                body["parentObservationId"] = obs_id_map.get(
                    parent,
                    str(uuid.uuid4()),
                )

        out.append({
            "id": str(uuid.uuid4()),
            "timestamp": ts,
            "type": e["type"],
            "body": body,
        })

    return {"batch": out}


async def worker(
    worker_id: int,
    client: httpx.AsyncClient,
    host: str,
    auth: str,
    template: list[dict],
    batch_size: int,
    request_counter: list[int],
    total_requests: int,
    metrics: Metrics,
    lock: asyncio.Lock,
):
    while True:
        async with lock:
            if request_counter[0] >= total_requests:
                return

            request_counter[0] += 1

        payload = build_request_batch(template, batch_size)

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

            latency = time.perf_counter() - started

            metrics.record_success(
                response.status_code,
                latency,
            )

        except Exception as exc:
            metrics.record_exception(exc)


def percentile(sorted_values, p):
    if not sorted_values:
        return 0

    idx = min(
        len(sorted_values) - 1,
        int(len(sorted_values) * p),
    )

    return sorted_values[idx]


async def main(
    total: int,
    concurrency: int,
    batch_size: int,
    trace_file: str,
):
    host = os.getenv(
        "LANGFUSE_HOST",
        "http://localhost:3000",
    )

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    auth = base64.b64encode(
        f"{public_key}:{secret_key}".encode()
    ).decode()

    template = load_template(trace_file)

    if batch_size > len(template):
        batch_size = len(template)

    # print payload stats up-front so numbers are apples-to-apples
    sample_payload = build_request_batch(template, batch_size)
    sample_bytes = len(json.dumps(sample_payload).encode())

    print(
        f"template: {len(template)} events "
        f"loaded from {trace_file}"
    )
    print(
        f"per-request payload: {batch_size} events, "
        f"~{sample_bytes / 1024:.1f} KiB json"
    )
    print()

    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )

    timeout = httpx.Timeout(
        connect=10.0,
        read=60.0,
        write=60.0,
        pool=10.0,
    )

    metrics = Metrics()

    request_counter = [0]
    lock = asyncio.Lock()

    total_events = total * batch_size

    print(
        f"firing {total} requests "
        f"(concurrency={concurrency}, batch_size={batch_size}) "
        f"= {total_events} events total ..."
    )

    started = time.perf_counter()

    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        http2=False,
    ) as client:

        workers = [
            worker(
                worker_id=i,
                client=client,
                host=host,
                auth=auth,
                template=template,
                batch_size=batch_size,
                request_counter=request_counter,
                total_requests=total,
                metrics=metrics,
                lock=lock,
            )
            for i in range(concurrency)
        ]

        await asyncio.gather(*workers)

    elapsed = time.perf_counter() - started

    total_completed = sum(metrics.status_counts.values())

    errors = sum(
        count
        for code, count in metrics.status_counts.items()
        if code == "exception"
        or (isinstance(code, int) and code >= 400)
    )

    successes = total_completed - errors

    print()
    print(
        f"done — {total_completed} requests "
        f"({total_completed * batch_size} events) "
        f"in {elapsed:.2f}s"
    )

    print(
        f"request throughput: "
        f"{total_completed / elapsed:.2f} req/s"
    )

    print(
        f"event throughput: "
        f"{(total_completed * batch_size) / elapsed:.2f} events/s"
    )

    print(
        f"success={successes} "
        f"errors={errors}"
    )

    print()

    for code, count in sorted(
        metrics.status_counts.items(),
        key=lambda x: str(x[0]),
    ):
        print(f"{code}: {count}")

    if metrics.latencies:
        metrics.latencies.sort()

        p50 = percentile(metrics.latencies, 0.50)
        p90 = percentile(metrics.latencies, 0.90)
        p95 = percentile(metrics.latencies, 0.95)
        p99 = percentile(metrics.latencies, 0.99)

        avg = sum(metrics.latencies) / len(metrics.latencies)

        print()
        print(f"avg={avg*1000:.1f}ms")
        print(f"p50={p50*1000:.1f}ms")
        print(f"p90={p90*1000:.1f}ms")
        print(f"p95={p95*1000:.1f}ms")
        print(f"p99={p99*1000:.1f}ms")
        print(f"max={metrics.latencies[-1]*1000:.1f}ms")

    if metrics.exceptions:
        print()
        print("sample exceptions:")

        for exc in metrics.exceptions[:5]:
            print(f"  {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--total", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="events per POST — trace.py uses 50",
    )
    parser.add_argument(
        "--trace-file",
        default="traces/interview/trace.json",
        help="langfuse-native trace file to use as payload template",
    )

    args = parser.parse_args()

    asyncio.run(
        main(
            total=args.total,
            concurrency=args.concurrency,
            batch_size=args.batch_size,
            trace_file=args.trace_file,
        )
    )