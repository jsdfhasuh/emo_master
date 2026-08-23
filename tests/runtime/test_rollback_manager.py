from emo_master.apps.runtime.context.rollback_manager import RollbackManager, ReleaseRecord


def testRollbackManagerSelectsLatestSuccessful() -> None:
  manager = RollbackManager()
  records = [
    ReleaseRecord(releaseId="r1", success=True, createdAt="2026-01-01T00:00:00Z", runtimeMin="0.1.0", runtimeMax="1.x"),
    ReleaseRecord(releaseId="r2", success=False, createdAt="2026-01-02T00:00:00Z", runtimeMin="0.1.0", runtimeMax="1.x"),
    ReleaseRecord(releaseId="r3", success=True, createdAt="2026-01-03T00:00:00Z", runtimeMin="0.1.0", runtimeMax="1.x")
  ]

  selected = manager.selectRollbackTarget(records, currentCoreVersion="0.1.0")
  assert selected is not None
  assert selected.releaseId == "r3"
