from functools import lru_cache


@lru_cache(maxsize=128)
def icon(name: str, color: str = "#475569"):
  from PySide2.QtCore import QByteArray, Qt
  from PySide2.QtGui import QIcon, QPainter, QPixmap
  from PySide2.QtSvg import QSvgRenderer
  from emo_master.apps.designer.ui._lucide_data import SVG_ICONS

  svg = SVG_ICONS.get(name, SVG_ICONS["layout-grid"])
  result = QIcon()
  for mode, tint in ((QIcon.Normal, color), (QIcon.Disabled, "#a1a8b3")):
    renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", tint).encode("utf-8")))
    for size in (16, 20, 24, 32, 40, 48, 64):
      pixmap = QPixmap(size, size)
      pixmap.fill(Qt.transparent)
      painter = QPainter(pixmap)
      renderer.render(painter)
      painter.end()
      result.addPixmap(pixmap, mode)
  return result


def operatorIcon(iconKey: str):
  return icon({"source": "camera", "edge": "scan-line", "measure": "ruler",
               "output": "upload", "flow": "git-branch"}.get(iconKey, "layout-grid"))


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
