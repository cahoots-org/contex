"""Golden eval dataset: documents + queries tagged by data-shape (spec §5.1).

Shapes: config_keys, code_symbols, error_ids, codenames, prose.
Each query's `relevant` set names the node_keys that genuinely satisfy it.
"""

DOCUMENTS = [
    # config_keys
    {"node_key": "cfg_timeout", "shape": "config_keys",
     "description": "service request timeout in milliseconds",
     "data_original": "SERVICE_TIMEOUT_MS=30000"},
    {"node_key": "cfg_retry", "shape": "config_keys",
     "description": "service retry backoff in milliseconds",
     "data_original": "SERVICE_RETRY_MS=1000"},
    # code_symbols
    {"node_key": "sym_get_user_by_id", "shape": "code_symbols",
     "description": "fetch a user record by primary key",
     "data_original": "def getUserById(user_id): ..."},
    {"node_key": "sym_get_user_by_email", "shape": "code_symbols",
     "description": "fetch a user record by email address",
     "data_original": "def getUserByEmail(email): ..."},
    # error_ids
    {"node_key": "err_conn_refused", "shape": "error_ids",
     "description": "database connection was refused",
     "data_original": "ERR_CONN_REFUSED: could not connect to postgres"},
    {"node_key": "err_503", "shape": "error_ids",
     "description": "service temporarily unavailable",
     "data_original": "HTTP 503 Service Unavailable from upstream"},
    # codenames (out-of-vocab)
    {"node_key": "svc_falcon", "shape": "codenames",
     "description": "internal billing service",
     "data_original": "Project Falcon owns invoicing and payment capture"},
    {"node_key": "svc_kestrel", "shape": "codenames",
     "description": "internal notification service",
     "data_original": "Project Kestrel owns email and push delivery"},
    # prose
    {"node_key": "doc_testing", "shape": "prose",
     "description": "testing requirements and coverage policy",
     "data_original": "All pull requests must maintain at least 80% line coverage."},
    {"node_key": "doc_style", "shape": "prose",
     "description": "code style and formatting standards",
     "data_original": "Use black for formatting and ruff for linting on every commit."},
]

QUERIES = [
    {"query": "SERVICE_TIMEOUT_MS", "shape": "config_keys", "relevant": {"cfg_timeout"}},
    {"query": "how long before a request times out", "shape": "config_keys", "relevant": {"cfg_timeout"}},
    {"query": "getUserByEmail", "shape": "code_symbols", "relevant": {"sym_get_user_by_email"}},
    {"query": "ERR_CONN_REFUSED", "shape": "error_ids", "relevant": {"err_conn_refused"}},
    {"query": "service unavailable upstream", "shape": "error_ids", "relevant": {"err_503"}},
    {"query": "Project Falcon", "shape": "codenames", "relevant": {"svc_falcon"}},
    {"query": "which service handles invoicing", "shape": "codenames", "relevant": {"svc_falcon"}},
    {"query": "what is our test coverage requirement", "shape": "prose", "relevant": {"doc_testing"}},
]
