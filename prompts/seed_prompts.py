import json
import os

from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()

client = Langfuse(
    host       = os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key = os.getenv("LANGFUSE_SECRET_KEY"),
)

with open("seed_prompts.json") as f:
    PROMPTS = json.load(f)


def main():
    for p in PROMPTS:
        print(f"  creating: {p['name']}")
        client.create_prompt(
            name   = p["name"],
            type   = p["type"],
            prompt = p["prompt"],
            labels = p.get("labels", []),
            config = p.get("config", {}),
        )

    client.flush()
    print(f"\ndone — {len(PROMPTS)} prompts created.")


if __name__ == "__main__":
    main()