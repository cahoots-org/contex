from src.core.database import _alembic_config


def test_percent_encoded_url_survives_alembic_config():
    url = "postgresql+asyncpg://contex:p%40ss%2Fword@db:5432/contex"
    assert _alembic_config(url).get_main_option("sqlalchemy.url") == url
