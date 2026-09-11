from pathlib import Path


def readAppQss() -> str:
    return Path("src/emo_master/apps/designer/ui/styles/app.qss").read_text(encoding="utf-8")


def testAppQssUsesLightWorkstationPalette() -> None:
    qss = readAppQss()
    assert "background: #ffffff" in qss
    assert "#2563eb" in qss
    assert "#f68656" not in qss
    assert "qlineargradient" not in qss
    assert "min-width: 96px" not in qss


def testAppQssHighlightsSelectedWorkflowTab() -> None:
    qss = readAppQss()
    selected = qss.split("QTabWidget#workflowTabs QTabBar::tab:selected", 1)[1].split("}", 1)[0]
    assert "font-weight: 600" in selected
    assert "border-bottom-color: #2563eb" in selected


def testAppQssIncludesOfflineControlIconsAndWarningStates() -> None:
    qss = readAppQss()
    assert "QLabel#workflowPackageWarnings" in qss
    assert "url(:/designer/chevron-down.svg)" in qss
    assert "QToolButton#primaryButton:disabled" in qss
