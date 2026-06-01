import argparse
import asyncio
import base64
import os
import random
import time
from collections import Counter

import httpx
from dotenv import load_dotenv

load_dotenv()

PROMPT_NAMES = [
    "customer-support-agent",
    "code-review-assistant",
    "medical-note-summarizer",
    "legal-contract-analyzer",
    "sql-query-generator",
    "financial-earnings-analyst",
    "onboarding-email-writer",
    "interview-question-generator",
    "data-extraction-structured-output",
    "incident-postmortem-writer",
]


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


async def worker(
    worker_id: int,
    client: httpx.AsyncClient,
    host: str,
    auth: str,
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

        prompt_name = random.choice(PROMPT_NAMES)

        started = time.perf_counter()

        try:
            response = await client.get(
                f"{host}/api/public/v2/prompts/{prompt_name}",
                params={"label": "production"},
                headers={"Authorization": f"Basic {auth}"},
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


async def main(total: int, concurrency: int):
    host = os.getenv(
        "LANGFUSE_HOST",
        "http://localhost:3000",
    )

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    auth = base64.b64encode(
        f"{public_key}:{secret_key}".encode()
    ).decode()

    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )

    timeout = httpx.Timeout(
        connect=10.0,
        read=30.0,
        write=30.0,
        pool=10.0,
    )

    metrics = Metrics()

    request_counter = [0]
    lock = asyncio.Lock()

    print(
        f"firing {total} requests "
        f"(concurrency={concurrency}) ..."
    )

    started = time.perf_counter()

    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        http2=False,  # set True if server supports HTTP/2
    ) as client:

        workers = [
            worker(
                worker_id=i,
                client=client,
                host=host,
                auth=auth,
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
        or (
            isinstance(code, int)
            and code >= 400
        )
    )

    successes = total_completed - errors

    print()
    print(
        f"done — {total_completed} requests "
        f"in {elapsed:.2f}s"
    )

    print(
        f"throughput: "
        f"{total_completed / elapsed:.2f} req/s"
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

        avg = (
            sum(metrics.latencies)
            / len(metrics.latencies)
        )

        print()
        print(
            f"avg={avg*1000:.1f}ms"
        )
        print(
            f"p50={p50*1000:.1f}ms"
        )
        print(
            f"p90={p90*1000:.1f}ms"
        )
        print(
            f"p95={p95*1000:.1f}ms"
        )
        print(
            f"p99={p99*1000:.1f}ms"
        )
        print(
            f"max={metrics.latencies[-1]*1000:.1f}ms"
        )

    if metrics.exceptions:
        print()
        print("sample exceptions:")

        for exc in metrics.exceptions[:5]:
            print(
                f"  {type(exc).__name__}: {exc}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--total",
        type=int,
        default=10000,
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=100,
    )

    args = parser.parse_args()

    asyncio.run(
        main(
            total=args.total,
            concurrency=args.concurrency,
        )
    )
