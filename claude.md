this repo is a self-hosting poc for langfuse — an llm observability platform. the goal is to run it on our own infra for data protection, cost, and vendor independence.

## architecture

two stateless app containers backed by four external data stores:

| component       | what is does                                                                          | stateful? |
| --------------- | ------------------------------------------------------------------------------------- | --------- |
| langfuse-web    | next.js app. serves the ui and the public api that sdk send traces to.                | no        |
| langfuse-worker | background processor. reads jobs from redis, writes processed traces to clickhouse.   | no        |
| postgres        | holds app config: users, projects, api keys, settings, evaluation configs.            | yes       |
| clickhouse      | holds the actual trace data. this is the big one (high-volume, time-series).          | yes       |
| redis           | job queue (bullmq) + cache + rate limit counters.                                     | yes       |
| s3              | blob store. raw trace events land here before workers read and process them.          | yes       |

**ingestion flow:** web pod writes raw event to s3 + enqueues a job in redis → worker batches writes to clickhouse. web never touches clickhouse on ingestion, which absorbs traffic spikes.

**prompt management flow:** prompts live in postgres, heavily cached in redis. clickhouse and worker are not involved.

## key files

- `docker-compose.prod.yml` — runs only web + worker; all data stores expected externally.
- `docker-compose.prod.with-clickhouse.yml` — co-located clickhouse for single-machine setups.
- `.env.prod.example` — all required environment variables.

## deployment approach

- postgres, redis, and clickhouse run as **systemd-managed docker compose services** on the host (`/etc/systemd/system/*.service`). they start on boot and restart on failure.
- langfuse web + worker run as a **separate docker compose stack**, started manually from the repo directory.
- config files for the data store services live under `opt/` in the repo and get copied to `/opt/` on the host before first run.

**preferred topology (option 2): clickhouse on its own ec2 instance, web + worker on a separate instance.**
- every web and worker container points to one shared `CLICKHOUSE_URL`.
- avoids split-brain: if you run multiple web/worker machines (e.g. for scaling), they all read and write the same clickhouse, so every request sees every trace.
- option 1 (co-located) breaks the moment you add a second web/worker machine — each machine would have its own clickhouse with only a subset of traces.

**clickhouse memory constraint:** minimum 1 gb ram required — the migration step alone consumes ~519 mb. a hard cap of 512 mb causes `memory limit exceeded` on startup. `docker-compose.prod.with-clickhouse.yml` sets a 1 gb reservation / 2 gb limit.

**s3 is the source of truth.** every trace event lands in s3 before clickhouse. clickhouse data is fully replayable, so clickhouse can be sized small, old data deleted (15–30 day ttl), and rebuilt from s3 if ever needed.