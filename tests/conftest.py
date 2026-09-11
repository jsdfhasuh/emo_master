from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolatedRuntimeData(tmp_path, monkeypatch):
    # Tests using RuntimeService() must not acquire the live application's lock.
    monkeypatch.setenv("EMO_RUNTIME_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.delenv("EMO_RUNTIME_DB_PATH", raising=False)
