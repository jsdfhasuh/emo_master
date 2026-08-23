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
