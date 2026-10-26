def testPackageImport() -> None:
  import emo_master
  from emo_master.app_version import APP_VERSION

  assert emo_master.__version__ == APP_VERSION
  assert isinstance(APP_VERSION, str) and APP_VERSION
