import os

from emo_master.apps.designer.main import _configureHighDpi, resolveRuntimeTarget


def testResolveRuntimeTargetUsesEnvVar() -> None:
  os.environ["EMO_RUNTIME_TARGET"] = "127.0.0.1:50051"
  assert resolveRuntimeTarget() == "127.0.0.1:50051"


def testConfigureHighDpiRunsBeforeApplicationCreation() -> None:
  configuredAttributes: list[tuple[object, bool]] = []
  configuredPolicies: list[object] = []

  class FakeCoreApplication:
    @staticmethod
    def instance():
      return None

    @staticmethod
    def setAttribute(attribute, enabled: bool) -> None:
      configuredAttributes.append((attribute, enabled))

  class RoundingPolicy:
    PassThrough = "pass-through"

  class FakeQt:
    AA_EnableHighDpiScaling = "enable-scaling"
    AA_UseHighDpiPixmaps = "high-dpi-pixmaps"
    HighDpiScaleFactorRoundingPolicy = RoundingPolicy

  class FakeGuiApplication:
    @staticmethod
    def setHighDpiScaleFactorRoundingPolicy(policy) -> None:
      configuredPolicies.append(policy)

  assert _configureHighDpi(FakeCoreApplication, FakeQt, FakeGuiApplication) is True
  assert configuredAttributes == [
    ("enable-scaling", True),
    ("high-dpi-pixmaps", True),
  ]
  assert configuredPolicies == ["pass-through"]


def testConfigureHighDpiDoesNotMutateRunningApplication() -> None:
  configuredAttributes: list[tuple[object, bool]] = []

  class FakeCoreApplication:
    @staticmethod
    def instance():
      return object()

    @staticmethod
    def setAttribute(attribute, enabled: bool) -> None:
      configuredAttributes.append((attribute, enabled))

  class FakeQt:
    AA_EnableHighDpiScaling = "enable-scaling"
    AA_UseHighDpiPixmaps = "high-dpi-pixmaps"

  class FakeGuiApplication:
    pass

  assert _configureHighDpi(FakeCoreApplication, FakeQt, FakeGuiApplication) is False
  assert configuredAttributes == []
