import json, uuid, os, requests
from datetime import datetime, timezone

PUBLIC_KEY = os.environ["LANGFUSE_PUBLIC_KEY"]
SECRET_KEY = os.environ["LANGFUSE_SECRET_KEY"]
HOST       = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")

OBS_TYPE_MAP = {
    "GENERATION": "generation-create",
    "SPAN":       "span-create",
    "EVENT":      "event-create",
}

def norm_ts(ts):
    return str(ts).replace(" ", "T") if ts else None

with open("traces/interview/trace.json") as f:
    traces = json.load(f)

batch = []

for t in traces:
    # trace-level event
    batch.append({
        "id":        str(uuid.uuid4()),
        "type":      "trace-create",
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
        }.items() if v is not None}
    })

    # observation-level events (spans / generations)
    for obs in t.get("observations", []):
        obs_type = obs.get("type", "SPAN")
        batch.append({
            "id":        str(uuid.uuid4()),
            "type":      OBS_TYPE_MAP.get(obs_type, "span-create"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
            }.items() if v is not None}
        })

# send in batches of 50
auth  = requests.auth.HTTPBasicAuth(PUBLIC_KEY, SECRET_KEY)
BATCH = 50
for i in range(0, len(batch), BATCH):
    chunk = batch[i:i+BATCH]
    r = requests.post(f"{HOST}/api/public/ingestion", json={"batch": chunk}, auth=auth)
    print(f"batch {i//BATCH+1}: {r.status_code}  ({i+len(chunk)}/{len(batch)} events)")
    if r.status_code != 207:
        print(r.text)
