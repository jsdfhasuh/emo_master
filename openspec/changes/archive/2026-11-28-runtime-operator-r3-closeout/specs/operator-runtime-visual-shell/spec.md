# Operator Runtime 工具视觉收尾

## Purpose

在 R3 已有 `LIGHT_THEME` 和局部 QSS 基础上，补齐 Operator
自有工具的字体与常用 Qt 控件状态；不改变 Designer
和工程生产页的视觉所有权。

## ADDED Requirements

### Requirement: Operator tools use an installed sans-serif font fallback

Operator-owned shell and tools SHALL select the first available font
from the configured sans-serif preference order, including
`Microsoft YaHei UI`, `Microsoft YaHei` and `Noto Sans CJK SC`. If none
is installed, the application SHALL retain the platform default font.
The implementation MUST NOT download or bundle a new font as part of
this closeout.

#### Scenario: Run on a machine without Microsoft YaHei UI

-   **WHEN** the preferred font family is unavailable
-   **THEN** Operator selects the next installed supported sans-serif
    family
-   **AND** it does not force an unavailable font or fail to render text

### Requirement: Operator common controls share consistent states

Operator-scoped QSS SHALL style checkboxes, radio buttons, scrollbars,
source tabs, focus and disabled states consistently with `LIGHT_THEME`,
while preserving current control behavior and native Qt widgets.

#### Scenario: Use a checkbox or source tab

-   **WHEN** the user focuses, selects, disables or toggles an
    Operator-owned checkbox, radio button or source tab
-   **THEN** the visual state is distinguishable and consistent with the
    Operator theme
-   **AND** the widget remains an ordinary Qt control with its existing
    signals and behavior

### Requirement: Operator log source tabs use compact sizing

The Operator log source bar SHALL present its three existing sources as
compact, left-aligned tabs rather than distributing them evenly across
the full width. Source names and order SHALL remain unchanged.

#### Scenario: Open the Operator log tool

-   **WHEN** the log tool is displayed at its normal operating size
-   **THEN** the three source tabs occupy only the space needed for
    their labels and padding
-   **AND** the Job workbench retains the remaining horizontal space

### Requirement: Operator visual changes remain locally scoped

The closeout styling SHALL remain scoped to Operator-owned widgets. It
SHALL NOT change Designer's visible layout or default shared widget
appearance, RuntimePages appearance or project-authored production-page
QSS.

#### Scenario: Open a project-authored production page after visiting Operator tools

-   **WHEN** the user opens and returns from StartupPage, Job logs or
    device management
-   **THEN** the project-authored page retains its configured appearance
-   **AND** shared Designer widgets retain their prior appearance
