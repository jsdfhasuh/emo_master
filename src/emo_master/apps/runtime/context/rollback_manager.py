from dataclasses import dataclass


@dataclass(frozen=True)
class ReleaseRecord:
  releaseId: str
  success: bool
  createdAt: str
  runtimeMin: str
  runtimeMax: str


class RollbackManager:
  def selectRollbackTarget(
    self,
    releases: list[ReleaseRecord],
    currentCoreVersion: str
  ) -> ReleaseRecord | None:
    compatible = [
      release for release in releases
      if release.success and self._isCompatible(currentCoreVersion, release.runtimeMin, release.runtimeMax)
    ]
    if not compatible:
      return None
    compatible.sort(key=lambda record: record.createdAt, reverse=True)
    return compatible[0]

  def _isCompatible(self, coreVersion: str, runtimeMin: str, runtimeMax: str) -> bool:
    coreMajor = int(coreVersion.split(".")[0])
    minMajor = int(runtimeMin.split(".")[0])
    if coreMajor < minMajor:
      return False

    if runtimeMax.endswith(".x"):
      maxMajor = int(runtimeMax.split(".")[0])
      return coreMajor <= maxMajor

    maxMajor = int(runtimeMax.split(".")[0])
    return coreMajor <= maxMajor
