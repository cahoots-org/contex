-- Create the database used by the test suite (see tests/conftest.py).
-- Postgres runs every file in /docker-entrypoint-initdb.d once, on first
-- container init, as POSTGRES_USER — so contex_test is owned by contex.
CREATE DATABASE contex_test;
