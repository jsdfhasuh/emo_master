from emo_master.ui.icons import fallbackIcon as fallbackIcon, icon as icon


def operatorIcon(iconKey: str):
  return icon({"source": "camera", "edge": "scan-line", "measure": "ruler",
               "output": "upload", "flow": "git-branch"}.get(iconKey, "layout-grid"))


def deviceIcon(iconKey: str):
  return icon({"plc-read": "cpu", "default": "layout-grid"}.get(iconKey, iconKey))


def getOperatorGlyph(iconKey: str) -> str:
  glyphs = {
    "source": "◉",
    "edge": "◇",
    "measure": "▦",
    "output": "⬒",
    "flow": "◍",
    "default": "◌"
  }
  return glyphs.get(iconKey, glyphs["default"])
