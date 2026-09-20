# Contex S3 connector

Bulk-loads text objects from an S3 bucket (or prefix) into a Contex project.
v1 is a **bulk snapshot**: a run lists and fetches every eligible object and
upserts it by object key, so re-running (e.g. on a cron) is the refresh story.

## What gets ingested

Text-like objects only: `.txt .md .markdown .json .csv .tsv .yaml .yml .html
.log .rst` by default (configurable via `content_types`). Binary objects (images,
archives, executables) are skipped. Objects over `max_object_bytes` (default
5 MB) are also skipped and logged.

`.json` objects are parsed into a dict (`data_format=json`). Everything else is
published as decoded UTF-8 (`data_format=text`). One object = one context item.

## Prerequisites

```
pip install -r connectors/s3/requirements.txt
```

AWS credentials are read from the standard chain (environment variables,
`~/.aws/credentials`, instance role). The connector only needs `s3:ListBucket`
and `s3:GetObject` on the target bucket. Set `source.endpoint_url` to point at an
S3-compatible store such as MinIO, Cloudflare R2, or LocalStack.

## Configuration

Copy `connector.yaml.example` to `connector.yaml` and edit:

```yaml
contex:
  url: http://localhost:8001/mcp
  project_id: my-app
  service_account_token: ${CONTEX_TOKEN}   # optional; omit if auth is off

source:
  bucket: my-knowledge-bucket
  prefix: docs/                             # optional; defaults to ""
  region: us-east-1
  # endpoint_url: http://localhost:9000    # optional; for S3-compatible stores
  # Optional explicit credentials (prefer env/instance role instead):
  # aws_access_key_id: ${AWS_ACCESS_KEY_ID}
  # aws_secret_access_key: ${AWS_SECRET_ACCESS_KEY}

batch_size: 200
max_object_bytes: 5242880                   # 5 MB; oversized objects are skipped

keys:
  include: []                               # glob allow-list over object key (empty = all)
  exclude: ["*.tmp", "archive/*"]

content_types: [".md", ".txt", ".json", ".csv", ".yaml", ".html"]
```

`${VAR}` references are expanded from the environment at load time.

## Running

```bash
python -m connectors.s3 --config connector.yaml
```

Progress is logged to stdout. The final line reports published item and batch
counts.

## Limitations

- **Not live sync.** Refresh by re-running; S3 event notifications are out of
  scope for v1.
- **Deletes don't propagate.** Objects removed from S3 are not removed from
  Contex on re-run.
- **Text objects only.** PDFs, images, Word documents, and other binary formats
  are not parsed.
- **No chunking.** Each object is one context item. Objects over
  `max_object_bytes` are skipped entirely.
