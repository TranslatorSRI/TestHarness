"""Fixtures: a migrated Postgres database and an app pointed at it.

Needs a Postgres the tests may wipe, given as RADIATOR_TEST_DATABASE_URL, eg
postgresql+psycopg://postgres@localhost:5432/radiator_test
"""

import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from radiator.app import create_app
from radiator.config import Settings

HERE = os.path.dirname(__file__)
TOKEN = "test-token"
USERNAME = "translator"
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session")
def database_url():
    url = os.getenv("RADIATOR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RADIATOR_TEST_DATABASE_URL is not set")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    engine.dispose()
    config = Config(os.path.join(HERE, "..", "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        api_token=TOKEN,
        username=USERNAME,
        password=PASSWORD,
        session_secret="test-secret",
        secure_cookies=False,
    )


@pytest.fixture
def app(settings):
    app = create_app(settings)
    yield app
    with app.state.sessionmaker() as session:
        session.execute(
            text("TRUNCATE runs, asset_results, agent_results, performance_results")
        )
        session.commit()


@pytest.fixture
def client(app):
    """Unauthenticated."""
    return TestClient(app)


@pytest.fixture
def api(app):
    """Authenticated with the API token."""
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def browser(app):
    """Logged in through the login form, like a person."""
    client = TestClient(app)
    res = client.post(
        "/login",
        data={"username": USERNAME, "password": PASSWORD},
        follow_redirects=False,
    )
    assert res.status_code == 303
    return client
