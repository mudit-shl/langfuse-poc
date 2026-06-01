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


async def send_one(client: httpx.AsyncClient, host: str, auth: str) -> int:
    name = PROMPT_NAMES[random.randint(0, len(PROMPT_NAMES) - 1)]
    r = await client.get(
        f"{host}/api/public/v2/prompts/{name}",
        params={"label": "production"},
        headers={"Authorization": f"Basic {auth}"},
    )
    return r.status_code


async def main(total: int, concurrency: int):
    host       = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    auth       = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)

    print(f"firing {total} requests (concurrency={concurrency}) ...")
    t = time.perf_counter()

    async with httpx.AsyncClient(limits=limits, timeout=30.0) as client:
        results = await asyncio.gather(
            *[send_one(client, host, auth) for _ in range(total)],
            return_exceptions=True,
        )

    elapsed = time.perf_counter() - t

    status_counts = Counter()
    sample_exceptions = []
    for r in results:
        if isinstance(r, Exception):
            status_counts["exception"] += 1
            if len(sample_exceptions) < 3:
                sample_exceptions.append(r)
        else:
            status_counts[r] += 1

    errors = sum(v for k, v in status_counts.items() if k == "exception" or k >= 400)
    ok     = total - errors

    print(f"done — {total} requests in {elapsed:.1f}s ({total/elapsed:.0f} req/s)")
    print(f"  ok={ok}  errors={errors}")
    for code, count in sorted(status_counts.items(), key=lambda x: str(x[0])):
        print(f"  {code}: {count}")
    for exc in sample_exceptions:
        print(f"  sample exception: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--total",       type=int, default=5000)
    parser.add_argument("--concurrency", type=int, default=500)
    args = parser.parse_args()
    asyncio.run(main(args.total, args.concurrency))