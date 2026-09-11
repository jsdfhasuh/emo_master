def testPackageImport() -> None:
  import emo_master

  assert emo_master.__version__ == "0.6.1"
