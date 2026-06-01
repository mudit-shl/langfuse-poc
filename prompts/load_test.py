import argparse
import asyncio
import os
import random
import time

from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()

client = Langfuse(
    host       = os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key = os.getenv("LANGFUSE_SECRET_KEY"),
)

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


async def send_one(i: int):
    name   = PROMPT_NAMES[random.randint(0, len(PROMPT_NAMES) - 1)]
    prompt = await asyncio.to_thread(lambda: client.get_prompt(name, cache_ttl_seconds=0))
    prompt.compile()


async def main(total: int):
    print(f"firing {total} requests ...")
    t = time.perf_counter()
    await asyncio.gather(*[send_one(i) for i in range(total)])
    elapsed = time.perf_counter() - t
    print(f"done — {total} requests in {elapsed:.1f}s ({total/elapsed:.0f} req/s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=5000)
    args = parser.parse_args()
    asyncio.run(main(args.total))