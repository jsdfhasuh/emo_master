import pytest

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.service import PresentationService
from examples.runtime_pages_p2 import sampleProject




@pytest.fixture
def channel(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / "runtime.sqlite3", workspaceRoot=tmp_path / "legacy-jobs")
    service = PresentationService(runtime, tmp_path / "display")
    try:
        yield service
    finally:
        runtime.close()
        service.close()


@pytest.fixture
def sample():
    return sampleProject
