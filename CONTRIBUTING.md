# Contributing to Contex

Thanks for helping. Open an issue to discuss anything larger than a small fix
before you start, and be respectful in issues and reviews.

## Local setup

You need Python 3.12 and Docker.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

Contex needs ParadeDB (Postgres with `pg_search` and `pgvector`); stock Postgres
images won't work. Run one on a spare port, with a second database for the tests:

```bash
docker run -d --name contex-db -p 55432:5432 \
  -e POSTGRES_DB=contex -e POSTGRES_USER=contex -e POSTGRES_PASSWORD=contex_password \
  paradedb/paradedb:0.25.9-pg18
docker exec contex-db psql -U contex -d contex -c 'CREATE DATABASE contex_test'
```

## Run the server

```bash
export DATABASE_URL=postgresql+asyncpg://contex:contex_password@localhost:55432/contex
python main.py
```

It serves on `http://127.0.0.1:8001` with auth off. Settings come from the
environment only; `python main.py` doesn't read `.env`. To use one, load it
into your shell first with `set -a; . ./.env; set +a`. See
[.env.example](.env.example) for every setting.

To run the whole stack in Docker instead, use `docker compose up`.

## Tests

```bash
DATABASE_URL=postgresql+asyncpg://contex:contex_password@localhost:55432/contex_test pytest tests/
```

Each connector has its own dependencies and tests:

```bash
pip install -r connectors/s3/requirements-dev.txt
pytest connectors/s3
```

Add a test that fails without your change. CI runs both suites, boots the
Docker image, and runs the security scans.

## Style

Lint the files you touch with `ruff check`. Keep imports at the top of the
module, docstrings short, and comments about what the code does rather than how
it got there.

## Pull requests

Branch from `main`, keep each PR to one change, and write commit messages with
an imperative subject line and a body that says why. Link the issue the PR
fixes.

## License

By contributing, you agree that your contributions are licensed under the MIT
License.
