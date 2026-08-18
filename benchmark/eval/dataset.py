"""Golden eval dataset: documents + queries tagged by data-shape (spec §5.1).

Shapes: config_keys, code_symbols, error_ids, codenames, prose.
Each query's `relevant` set names the node_keys that genuinely satisfy it.

The exact-token shapes (config_keys, error_ids, codenames) deliberately
include ADVERSARIAL DISTRACTORS: decoy documents that share surface tokens
with, or are semantically near, the true relevant document. These decoys
tempt a dense model (all-MiniLM-L6-v2) into mis-ranking the wrong doc above
the exact match, so the eval genuinely discriminates between pure-vector and
lexical-hybrid retrieval instead of trivially scoring 1.00 everywhere.
"""

DOCUMENTS = [
    # ------------------------------------------------------------------ #
    # config_keys  (true target for "SERVICE_TIMEOUT_MS": cfg_timeout)
    # ------------------------------------------------------------------ #
    {"node_key": "cfg_timeout", "shape": "config_keys",
     "description": "service request timeout in milliseconds",
     "data_original": "SERVICE_TIMEOUT_MS=30000"},
    {"node_key": "cfg_retry", "shape": "config_keys",
     "description": "service retry backoff in milliseconds",
     "data_original": "SERVICE_RETRY_MS=1000"},
    # distractors: near-miss timeout keys sharing tokens / semantics
    {"node_key": "cfg_timeout_prod", "shape": "config_keys",
     "description": "production override for the service request timeout in milliseconds",
     "data_original": "SERVICE_TIMEOUT_MS=5000"},
    {"node_key": "cfg_connect_timeout", "shape": "config_keys",
     "description": "database connection timeout in milliseconds",
     "data_original": "DB_CONNECT_TIMEOUT_MS=2000"},
    {"node_key": "cfg_read_timeout", "shape": "config_keys",
     "description": "upstream read timeout in milliseconds",
     "data_original": "UPSTREAM_READ_TIMEOUT_MS=15000"},
    {"node_key": "cfg_idle_timeout", "shape": "config_keys",
     "description": "socket idle timeout in milliseconds",
     "data_original": "SOCKET_IDLE_TIMEOUT_MS=60000"},

    # ------------------------------------------------------------------ #
    # code_symbols  (true target for "getUserByEmail": sym_get_user_by_email)
    # ------------------------------------------------------------------ #
    {"node_key": "sym_get_user_by_id", "shape": "code_symbols",
     "description": "fetch a user record by primary key",
     "data_original": "def getUserById(user_id): ..."},
    {"node_key": "sym_get_user_by_email", "shape": "code_symbols",
     "description": "fetch a user record by email address",
     "data_original": "def getUserByEmail(email): ..."},
    # distractors: sibling accessors overlapping heavily on tokens/semantics
    {"node_key": "sym_find_user_by_email", "shape": "code_symbols",
     "description": "look up a user account using their email",
     "data_original": "def findUserByEmail(email): ..."},
    {"node_key": "sym_get_users_by_email_domain", "shape": "code_symbols",
     "description": "fetch all users sharing an email domain",
     "data_original": "def getUsersByEmailDomain(domain): ..."},

    # ------------------------------------------------------------------ #
    # error_ids  (true target for "ERR_CONN_REFUSED": err_conn_refused)
    # ------------------------------------------------------------------ #
    {"node_key": "err_conn_refused", "shape": "error_ids",
     "description": "database connection was refused",
     "data_original": "ERR_CONN_REFUSED: could not connect to postgres"},
    {"node_key": "err_503", "shape": "error_ids",
     "description": "service temporarily unavailable",
     "data_original": "HTTP 503 Service Unavailable from upstream"},
    # distractors: connection-ish errors competing with ERR_CONN_REFUSED
    {"node_key": "err_conn_timeout", "shape": "error_ids",
     "description": "database connection could not be established",
     "data_original": "ERR_CONN_TIMEOUT: ssl handshake failed while connecting to postgres"},
    {"node_key": "err_conn_reset", "shape": "error_ids",
     "description": "database connection was reset by peer",
     "data_original": "ERR_CONN_RESET: connection to postgres dropped mid-query"},
    {"node_key": "err_auth_refused", "shape": "error_ids",
     "description": "database authentication was refused",
     "data_original": "ERR_AUTH_REFUSED: password authentication failed for postgres user"},

    # ------------------------------------------------------------------ #
    # codenames (out-of-vocab)  (true target for "Project Falcon": svc_falcon)
    # ------------------------------------------------------------------ #
    {"node_key": "svc_falcon", "shape": "codenames",
     "description": "internal billing service",
     "data_original": "Project Falcon owns invoicing and payment capture"},
    {"node_key": "svc_kestrel", "shape": "codenames",
     "description": "internal notification service",
     "data_original": "Project Kestrel owns email and push delivery"},
    # distractors: token-overlap decoys that mention "Falcon" but are NOT it
    {"node_key": "svc_merlin", "shape": "codenames",
     "description": "internal analytics service",
     "data_original": "Project Falcon-X handles reporting and dashboards"},
    {"node_key": "svc_osprey", "shape": "codenames",
     "description": "internal deployment service",
     "data_original": "Falcon Heavy is the deployment pipeline for Project Osprey"},
    {"node_key": "svc_peregrine", "shape": "codenames",
     "description": "internal billing reconciliation service",
     "data_original": "Project Peregrine reconciles invoicing ledgers for the billing team nightly"},

    # ------------------------------------------------------------------ #
    # prose
    # ------------------------------------------------------------------ #
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
