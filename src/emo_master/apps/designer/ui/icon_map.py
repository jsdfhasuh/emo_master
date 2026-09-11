from functools import lru_cache


@lru_cache(maxsize=128)
def icon(name: str, color: str = "#475569"):
  from PySide2.QtCore import QByteArray, Qt
  from PySide2.QtGui import QIcon, QPainter, QPixmap
  from emo_master.apps.designer.ui._lucide_data import SVG_ICONS

  try:
    from PySide2.QtSvg import QSvgRenderer
    svg = SVG_ICONS.get(name, SVG_ICONS["layout-grid"])
    result = QIcon()
    for mode, tint in ((QIcon.Normal, color), (QIcon.Disabled, "#a1a8b3")):
      renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", tint).encode("utf-8")))
      if not renderer.isValid():
        return fallbackIcon(name, color)
      for size in (16, 20, 24, 32, 40, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
          renderer.render(painter)
        finally:
          painter.end()
        result.addPixmap(pixmap, mode)
    return result
  except Exception:
    return fallbackIcon(name, color)


def fallbackIcon(name: str = "default", color: str = "#475569"):
  from PySide2.QtCore import Qt
  from PySide2.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

  glyph = {"play": "\u25b6", "square": "\u25a0", "x": "\u00d7",
           "plus": "+", "minus": "-", "refresh-cw": "\u21bb"}.get(name, "\u25c7")
  result = QIcon()
  for size in (20, 40, 80):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
      font = QFont()
      font.setPixelSize(int(size * 0.8))
      painter.setFont(font)
      painter.setPen(QColor(color))
      painter.drawText(pixmap.rect(), Qt.AlignCenter, glyph)
    finally:
      painter.end()
    result.addPixmap(pixmap)
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
