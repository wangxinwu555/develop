import pytest
from fastapi.testclient import TestClient

from supportflow.api import create_app
from supportflow.config import Settings
from supportflow.ingest import build_index
from supportflow.service import AgentService


@pytest.fixture(scope="session")
def knowledge_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("knowledge")
    build_index(Settings(_env_file=None, embedding_provider="demo", knowledge_dir=path))
    return path


@pytest.fixture
def settings(tmp_path, knowledge_dir):
    return Settings(
        _env_file=None,
        agent_mode="demo",
        embedding_provider="demo",
        runtime_dir=tmp_path,
        knowledge_dir=knowledge_dir,
    )


@pytest.fixture
def service(settings):
    instance = AgentService(settings)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), headers={"X-Demo-Token": "demo-alice"}) as client:
        yield client
