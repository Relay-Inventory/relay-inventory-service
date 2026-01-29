import os
import sys
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_ENV = {
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "AWS_SESSION_TOKEN": "test-session",
    "AWS_DEFAULT_REGION": "us-east-1",
    "FWW_ENV": "test",
    "PARTS_TRADER_USER": "test-user",
    "PARTS_TRADER_PASS": "test-pass",
}


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def tests_data_dir(project_root: Path) -> Path:
    return project_root / "tests" / "mock"


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    for key, value in DEFAULT_ENV.items():
        monkeypatch.setenv(key, value)
    yield


@pytest.fixture(autouse=True)
def stubbed_boto3_session(monkeypatch, request):
    if "relay_inventory" in request.node.nodeid:
        yield None
        return
    import boto3

    class _StubSession:
        def __init__(self):
            self.clients: Dict[str, MagicMock] = {}

        def client(self, service_name: str, region_name: str | None = None):
            client = self.clients.get(service_name)
            if not client:
                client = MagicMock(name=f"{service_name}_client")
                self.clients[service_name] = client
            return client

    session = _StubSession()
    monkeypatch.setattr(boto3, "Session", lambda **_: session)
    return session


@pytest.fixture
def fake_selenium_element():
    return MagicMock()


@pytest.fixture
def freezer():
    with freeze_time("2020-01-01T00:00:00Z") as frozen_datetime:
        yield frozen_datetime
