# operator-runtime-visual-shell Specification

## Purpose
定义 Operator 原生 Qt 外观及工具页操作，覆盖起始设置、生产控件、日志、设备和工程样式作用域。

## Requirements

### Requirement: Operator shell uses the shared light visual language

Operator SHALL reuse `emo_panel_protocol.theme.LIGHT_THEME` for page
backgrounds, surfaces, borders, text roles, primary actions and semantic
states. It SHALL NOT load the Designer application's full global QSS.

#### Scenario: Open the Operator startup/settings page

-   **WHEN** the user opens the startup/settings page
-   **THEN** the page uses the same light visual language as
    RuntimePages
-   **AND** project, recent logs, local directory and remote access are
    presented with clear card/section hierarchy
-   **AND** long paths remain readable and do not force horizontal
    scrolling

### Requirement: Existing Operator navigation remains unchanged

Operator SHALL retain the existing `QStackedWidget`, business page,
StartupPage, RuntimeLogView, tool history, back button and focus
restoration. New views SHALL be added inside existing tools rather than
creating a new global navigation shell.

#### Scenario: Open settings or logs from a production page

-   **WHEN** the user opens settings or logs from a production page
-   **THEN** the existing tool navigation and Back behavior are used
-   **AND** returning restores the previous page and viewing context

### Requirement: Host controls retain their business behavior across reparenting

Operator SHALL preserve the existing project, start/stop, view-run and
device actions. Styling SHALL remain correct when `technicalHost` is
hosted in the Operator root layout or moved into
`RuntimePages.diagnostics`.

#### Scenario: Render host controls in either host location

-   **WHEN** the production page configuration places `technicalHost` in
    RuntimePages diagnostics or leaves it in the Operator root
-   **THEN** the host controls retain their visual hierarchy and remain
    usable
-   **AND** both host locations use the same Runtime start-availability
    result for the target workflow, the existing StartJob preflight and
    the production controls' explicit stop target

### Requirement: Operator tools use scoped styles

Operator-specific QSS SHALL be scoped to Operator-owned widgets and
SHALL NOT override project-authored production-page QSS, RuntimePages
local appearance or shared Designer widget defaults.

#### Scenario: Display a project-authored production page

-   **WHEN** a project uses custom page headers, buttons, cards or
    component QSS
-   **THEN** Operator styles affect only the Operator shell and tools
-   **AND** the project page appearance and business behavior remain
    unchanged

#### Scenario: Display shared communication widgets

-   **WHEN** Operator displays shared communication/diagnostic widgets
-   **THEN** Operator styling is scoped to its own dialog/widget subtree
-   **AND** Designer default appearance is unchanged

### Requirement: Operator visual delivery is verified in native Qt

Operator visual completion SHALL be verified with native Qt screenshots
and representative desktop interaction, not only HTML prototypes or
offscreen tests.

#### Scenario: Review representative native views

-   **WHEN** the implementation is reviewed
-   **THEN** startup/settings, production, log and device views have
    representative native Qt screenshots and interaction evidence at
    their normal operating sizes, using the default 1060×800 logical main
    window as the main-window baseline
-   **AND** primary actions, long paths, scroll areas and return
    controls remain usable

#### Scenario: Review the startup page at a narrower window

-   **WHEN** the startup/settings page is resized to a representative
    900×700 logical main window
-   **THEN** its existing responsive layout remains usable and essential
    project, save and return actions can be reached
-   **AND** additional narrow-window checks for other views are required
    only when their layout or responsive behavior changes, or an observed
    issue calls for them
-   **AND** this change does not require every view to repeat a fixed
    two-size screenshot matrix

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
