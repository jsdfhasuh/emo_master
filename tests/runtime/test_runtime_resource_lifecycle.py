from emo_master.apps.runtime.grpc_server.service import RuntimeService


def testRuntimeServiceCloseIsIdempotentAndReleasesRuntimeDataLock(tmp_path) -> None:
    dbPath = tmp_path / "runtime.db"
    workspaceRoot = tmp_path / "jobs"
    service = RuntimeService(dbPath=dbPath, workspaceRoot=workspaceRoot)

    service.close()
    service.close()

    replacement = RuntimeService(dbPath=dbPath, workspaceRoot=workspaceRoot)
    replacement.close()
