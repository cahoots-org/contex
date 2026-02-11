#!/bin/bash
set -euo pipefail

# Fix volume permissions for Railway
sudo chown -R 1000 /usr/share/opensearch/data

# Run the original OpenSearch entrypoint
exec /usr/share/opensearch/opensearch-docker-entrypoint.sh opensearch
