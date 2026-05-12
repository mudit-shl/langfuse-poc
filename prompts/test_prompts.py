import os
from   dotenv   import load_dotenv
from   langfuse import Langfuse

load_dotenv()

client = Langfuse(
    host       = os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
)


def create_prompt():
    print("Creating prompt...")
    prompt = client.create_prompt(
        name   = "my-first-prompt",
        prompt = "You are a helpful assistant. Answer the question: {{question}}",
        labels = ["production"]
    )
    print(f"Created prompt: {prompt.name} (version {prompt.version})")
    return prompt


def fetch_and_compile_prompt():
    print("\nFetching prompt...")
    prompt = client.get_prompt("my-first-prompt")
    print(f"Fetched prompt: {prompt.name} (version {prompt.version})")

    compiled = prompt.compile(question="What is Langfuse?")
    print(f"Compiled prompt: {compiled}")
    return prompt


def update_prompt():
    print("\nUpdating prompt (creating new version)...")
    prompt = client.create_prompt(
        name   = "my-first-prompt",
        prompt = "You are an expert assistant. Please answer this question thoroughly: {{question}}",
        labels = ["production"]
    )
    print(f"Updated prompt: {prompt.name} (version {prompt.version})")
    return prompt


def fetch_specific_version(version: int):
    print(f"\nFetching prompt at version {version}...")
    prompt   = client.get_prompt("my-first-prompt", version=version)
    compiled = prompt.compile(question="What is Langfuse?")
    print(f"Version {version} compiled: {compiled}")


if __name__ == "__main__":
    create_prompt()
    fetch_and_compile_prompt()
    update_prompt()
    fetch_and_compile_prompt()
    fetch_specific_version(version=1)

    print("\n✅ All done. Check http://localhost:3000 to see prompts in the UI.")