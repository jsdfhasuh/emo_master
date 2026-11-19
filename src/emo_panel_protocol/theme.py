"""Current light presentation theme, shared through the generated Panel SDK."""

from typing import TypedDict


class _TextRole(TypedDict):
    size: int
    weight: str
    color: str


class _LightTheme(TypedDict):
    page: str
    surface: str
    subtle: str
    border: str
    text: str
    muted: str
    primary: str
    dark: str
    foreground: dict[str, str]
    soft: dict[str, str]
    typography: dict[str, _TextRole]


LIGHT_THEME: _LightTheme = {
    'page': '#f3f5f9',
    'surface': '#ffffff',
    'subtle': '#eef2f8',
    'border': '#dce3ed',
    'text': '#17243a',
    'muted': '#53647b',
    'primary': '#2459d1',
    'dark': '#202939',
    'foreground': {'green': '#147044', 'red': '#a92235', 'amber': '#925300',
                   'blue': '#2459d1', 'neutral': '#53647b'},
    'soft': {'green': '#e8f7ee', 'red': '#fdeff0', 'amber': '#fff4dc',
             'blue': '#eaf0ff', 'neutral': '#eef2f8'},
    'typography': {
        'title': {'size': 13, 'weight': 'bold', 'color': 'default'},
        'value': {'size': 14, 'weight': 'normal', 'color': 'default'},
        'helper': {'size': 12, 'weight': 'normal', 'color': 'neutral'},
    },
}
