from pathlib import Path


def readAppQss() -> str:
  return Path("src/emo_master/apps/designer/ui/styles/app.qss").read_text(
    encoding="utf-8"
  )


def testAppQssUsesIndustrialMenuAndControlStyling() -> None:
  qss = readAppQss()
  assert "QMenuBar {" in qss
  assert "background: #41403b;" in qss
  assert "QMenu {" in qss
  assert "background: #41403b;" in qss
  assert "QLineEdit {" in qss or "QLineEdit," in qss
  assert "background: #ffffff;" in qss
  assert "QPushButton#secondaryButton," in qss or "QPushButton#secondaryButton {" in qss
  assert "background: #f7f6f6;" in qss
