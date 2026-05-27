This repo is a self-hosting POC for Langfuse — an LLM observability platform. The goal is to run it on our own infra for data protection, cost, and vendor independence.

Architecture: two stateless app containers (`langfuse-web`, `langfuse-worker`) backed by four external data stores (Postgres, ClickHouse, Redis, S3). `docker-compose.prod.yml` runs only the app services — all data stores are expected externally.
