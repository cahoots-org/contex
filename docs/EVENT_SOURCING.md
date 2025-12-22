# Event Sourcing in Contex

Contex uses event sourcing via PostgreSQL to maintain an immutable audit trail of all data changes. This document explains how to leverage event sourcing for debugging, compliance, disaster recovery, and more.

## Overview

Every data change in Contex is stored as an immutable event with:
- **Automatic sequencing** - Events ordered by timestamp and sequence number
- **Complete history** - Never deleted, always queryable
- **Time-travel queries** - Reconstruct state at any point in time
- **Agent catch-up** - New agents automatically receive historical context

## Event Structure

Each event contains three core fields:

```json
{
  "sequence": 1704067200000,
  "event_type": "data_published",
  "data": {
    "data_key": "api_config",
    "data": {
      "base_url": "https://api.example.com",
      "timeout": 30
    },
    "description": "API configuration and endpoints"
  }
}
```

### Field Reference

- **sequence**: Auto-incrementing sequence number per project
  - Monotonically increasing within each project
  - Used for ordering and catch-up queries

- **event_type**: Type of event that occurred
  - `data_published` - New data added or updated
  - `agent_registered` - Agent subscribed to project
  - More types may be added in future versions

- **data**: Complete event payload
  - Contains all information needed to understand what changed
  - Structure varies by event_type

## Querying Events

### Get All Events

```python
import httpx

response = await httpx.get(
    "http://localhost:8001/api/v1/projects/my-app/events",
    params={"since": "0", "count": 100}
)

# Response format
{
  "events": [
    {"sequence": 1, "event_type": "...", "data": {...}},
    {"sequence": 2, "event_type": "...", "data": {...}},
    ...
  ],
  "count": 42
}
```

### Get Events Since Sequence

```python
# Get only new events since last check
response = await httpx.get(
    "http://localhost:8001/api/v1/projects/my-app/events",
    params={"since": "100", "count": 50}
)
```

### Get Events in Time Range

```python
from datetime import datetime

# Query events and filter by timestamp
all_events = await httpx.get(
    "http://localhost:8001/api/v1/projects/my-app/events",
    params={"since": "0", "count": 10000}
)

# Filter to time range
start_time = datetime(2024, 1, 15, 15, 0)
end_time = datetime(2024, 1, 15, 16, 0)

events_in_range = [
    e for e in all_events["events"]
    if start_time <= datetime.fromisoformat(e["created_at"]) <= end_time
]

print(f"Found {len(events_in_range)} events between 3-4 PM")
```

## Use Cases

### 1. Time-Travel Debugging

**Problem**: An agent made a bad decision at 3:30 PM. What data did it have at that moment?

**Solution**: Reconstruct the agent's context at that exact time:

```python
import httpx
from datetime import datetime

# Get all events up to that point
events = await httpx.get(
    "http://localhost:8001/api/v1/projects/my-app/events",
    params={"since": "0", "count": 1000}
)

# Filter events before the target time
target_time = datetime(2024, 1, 15, 15, 30)
historical_events = [
    e for e in events["events"]
    if datetime.fromisoformat(e["created_at"]) <= target_time
]

# Reconstruct agent's context at that moment
context_at_time = {}
for event in historical_events:
    if event["event_type"] == "data_published":
        key = event["data"]["data_key"]
        context_at_time[key] = event["data"]["data"]

print(f"Agent had {len(context_at_time)} data sources at 3:30 PM")
print(f"API config was: {context_at_time.get('api_config')}")

# Now you can see exactly what the agent knew when it made the decision
```

### 2. Compliance & Auditing

Event sourcing provides complete audit trails required by regulations like SOX, GDPR, HIPAA, and SOC 2.

**What You Get:**

- Who published data and when
- What data existed at any point in time
- Which agents received which data
- Complete history that can't be altered

**Example Audit Query:**

```python
# Find all data published by a specific source
events = await get_all_events("my-app")

audit_trail = [
    {
        "timestamp": e["created_at"],
        "data_key": e["data"]["data_key"],
        "change_type": "published"
    }
    for e in events
    if e["event_type"] == "data_published"
]

# Export for compliance reporting
import csv
with open("audit_trail.csv", "w") as f:
    writer = csv.DictWriter(f, fieldnames=["timestamp", "data_key", "change_type"])
    writer.writeheader()
    writer.writerows(audit_trail)
```

### 3. Analytics & Insights

Analyze patterns in your event stream to understand system behavior.

**Most Frequently Updated Data:**

```python
from collections import Counter

events = await get_all_events("my-app")
data_keys = [
    e["data"]["data_key"]
    for e in events
    if e["event_type"] == "data_published"
]

most_common = Counter(data_keys).most_common(5)

print("Top 5 most updated data sources:")
for key, count in most_common:
    print(f"  {key}: {count} updates")
```

**Update Frequency Over Time:**

```python
from datetime import datetime
from collections import defaultdict

events = await get_all_events("my-app")

# Group by hour
updates_by_hour = defaultdict(int)
for e in events:
    if e["event_type"] == "data_published":
        dt = datetime.fromisoformat(e["created_at"])
        hour = dt.replace(minute=0, second=0, microsecond=0)
        updates_by_hour[hour] += 1

# Find peak hours
peak_hours = sorted(updates_by_hour.items(), key=lambda x: x[1], reverse=True)[:5]
print("Peak update hours:")
for hour, count in peak_hours:
    print(f"  {hour}: {count} updates")
```

### 4. Disaster Recovery

Export and replay events to recover from failures or migrate to new instances.

**Export Events:**

```bash
# Export all events to JSON file
curl "http://localhost:8001/api/v1/projects/my-app/events?since=0&count=10000" \
  > events-backup-$(date +%Y%m%d).json

# Verify backup
jq '.count' events-backup-*.json
```

**Replay on New Instance:**

```python
import httpx
import json

# Load backup
with open("events-backup.json") as f:
    backup = json.load(f)

# Replay data_published events
async with httpx.AsyncClient() as client:
    for event in backup["events"]:
        if event["event_type"] == "data_published":
            await client.post(
                "http://new-instance:8001/api/v1/data/publish",
                json=event["data"]
            )
            print(f"Replayed: {event['data']['data_key']}")

print(f"Replayed {len(backup['events'])} events")
```

### 5. Testing & Development

Clone production data into test environments for accurate issue reproduction.

```python
import httpx

# Export production events
prod_events = await httpx.get(
    "https://prod.contex.io/api/v1/projects/my-app/events",
    params={"since": "0", "count": 1000}
)

# Replay in test environment
async with httpx.AsyncClient() as client:
    for event in prod_events.json()["events"]:
        if event["event_type"] == "data_published":
            await client.post(
                "http://test.contex.local/api/v1/data/publish",
                json=event["data"]
            )

# Test environment now has identical data to production
```

### 6. New Agent Catch-Up

When registering a new agent, it automatically receives all relevant historical data, not just current state.

**How It Works:**

```python
# Register agent
registration = await httpx.post(
    "http://localhost:8001/api/v1/agents/register",
    json={
        "agent_id": "new-agent",
        "project_id": "my-app",
        "data_needs": ["API configuration", "database schemas"],
        "last_seen_sequence": "0"  # Get everything from the beginning
    }
)

# Agent receives all matching data that was ever published
matched_data = registration.json()["matched_data"]
print(f"Agent caught up with {len(matched_data)} historical data sources")
```

## Best Practices

### 1. Set Appropriate Count Limits

```python
# For large projects, use pagination
count_per_page = 100
since = "0"

all_events = []
while True:
    response = await httpx.get(
        f"http://localhost:8001/api/v1/projects/my-app/events",
        params={"since": since, "count": count_per_page}
    )

    events = response.json()["events"]
    if not events:
        break

    all_events.extend(events)
    since = str(events[-1]["sequence"])  # Last sequence becomes next 'since'

print(f"Retrieved {len(all_events)} total events")
```

### 2. Cache Event Data

```python
# Cache events locally to avoid repeated API calls
import json
from pathlib import Path
from datetime import datetime, timedelta

cache_file = Path("events_cache.json")
cache_age_hours = 1

def is_cache_fresh():
    if not cache_file.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
    return age < timedelta(hours=cache_age_hours)

async def get_events_cached(project_id):
    if is_cache_fresh():
        with open(cache_file) as f:
            return json.load(f)

    # Fetch fresh data
    response = await httpx.get(
        f"http://localhost:8001/api/v1/projects/{project_id}/events",
        params={"since": "0", "count": 10000}
    )
    events = response.json()

    # Cache it
    with open(cache_file, "w") as f:
        json.dump(events, f)

    return events
```

### 3. Monitor Event Count

```python
# Check how many events exist
events = await httpx.get(
    "http://localhost:8001/api/v1/projects/my-app/events",
    params={"since": "0", "count": 1}
)

# If count is large, consider data retention policies
total_count = events.json()["count"]
if total_count > 100000:
    print(f"Warning: {total_count} events in table, consider archiving old data")
```

### 4. Structure Event Queries Efficiently

```python
# Bad: Getting all events repeatedly
for i in range(10):
    all_events = await get_all_events()  # Wasteful
    process(all_events)

# Good: Get once, process multiple times
all_events = await get_all_events()
for i in range(10):
    process(all_events)

# Better: Stream processing
since = "0"
while True:
    events = await get_events_since(since, count=100)
    if not events:
        break
    process(events)
    since = str(events[-1]["sequence"])
```

## Performance Considerations

### Event Table Size

- PostgreSQL handles millions of events per project efficiently
- Indexed on `(project_id, sequence)` for fast range queries
- Storage: ~1KB per event (varies with payload size)

### Query Performance

- Recent event queries are fast (indexed lookup)
- Full table scans for old events should use pagination
- Use incremental queries when possible:

```python
# Efficient: Only get new events
last_sequence = load_checkpoint()
new_events = await get_events_since(last_sequence)
save_checkpoint(new_events[-1]["sequence"])
```

### Data Retention

For very large event stores, consider retention policies:

```python
# Archive old events to S3/object storage
from datetime import datetime, timedelta

cutoff = datetime.now() - timedelta(days=90)

events = await get_all_events("my-app")
old_events = [
    e for e in events
    if datetime.fromisoformat(e["created_at"]) < cutoff
]

# Archive to long-term storage
import boto3
s3 = boto3.client("s3")
s3.put_object(
    Bucket="contex-archives",
    Key=f"my-app/events-{cutoff.date()}.json",
    Body=json.dumps(old_events)
)

# Delete from database (via admin API or direct SQL)
# DELETE FROM events WHERE project_id = 'my-app' AND created_at < cutoff
```

## Troubleshooting

### Events Not Appearing

```python
# Check if events are being published
events = await httpx.get(
    "http://localhost:8001/api/v1/projects/my-app/events",
    params={"since": "0", "count": 10}
)

if events.json()["count"] == 0:
    print("No events found. Is data being published?")
else:
    print(f"Found {events.json()['count']} events")
    print(f"Latest: {events.json()['events'][-1]}")
```

### Sequence Number Gaps

Sequence numbers may have gaps if transactions fail. This is normal and expected - the ordering is preserved.

### Missing Historical Events

```python
# Ensure you're querying from the beginning
response = await httpx.get(
    "http://localhost:8001/api/v1/projects/my-app/events",
    params={"since": "0", "count": 1000}  # "0" means from beginning
)

# Check if you need pagination
if response.json()["count"] == 1000:
    print("Warning: Hit count limit, use pagination to get all events")
```

## Related Documentation

- [Database Setup](DATABASE.md) - PostgreSQL and pgvector configuration
- [RBAC](RBAC.md) - Access control for event queries
- [Metrics](METRICS.md) - Monitoring event stream health
