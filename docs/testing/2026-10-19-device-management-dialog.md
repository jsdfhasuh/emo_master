# Device Management Dialog Correction

Base source: `3396a25d34d1084b56fd8c4867450f0b62e26c64`.
Date: 2026-10-19. This record covers the local dialog change, not a new EXE.

## Provenance And Current Requirement

The embedded third workspace was introduced by
`c097c39934955ababc679f2fb704b50dba488030`, committed at
2026-10-18 14:10:58 +08:00. Its `showDevices()` adds the device QWidget to the
central stack and switches to it; the same commit adds third-workspace tests.
The later F02 upload repairs did not introduce this behavior.

The original supplied R2.1 ZIP was re-read: main-plan line 97 and handoff lines
17/69 explicitly require embedding the screenshot content in a third workspace,
not copying its modal shell. The archived plan was not silently rewritten.
The user's current explicit requirement supersedes that placement: device
management must open as a separate dialog, not replace the main workspace.

## Implementation

- Keep only flow/page design in the workspace switch. A menu-bar Device Management
  action opens the existing device editor as one parent-owned, window-modal QDialog.
- Do not add the dialog to the central stack, change the active page or hide an
  existing preview observer. Repeated activation raises the same dialog.
- Close button, Esc and native close retain valid property changes in the draft;
  invalid properties remain editable. Closing does not save disk or write Runtime.
- Ordinary close hides the dialog and allows an in-flight metadata read to finish;
  reopening keeps the reader usable. Project replacement retires the old timer,
  reader and dialog without applying old fields to the newly loaded project.
- Preserve existing device controls, explicit confirmations and configuration
  APIs. Opening the dialog only reads metadata, not devices or PLC registers.

## Verification

Windows / Python 3.10 / PySide2, `QT_QPA_PLATFORM=windows`.
Evidence: `build/device-dialog/` (local, ignored).

The baseline dialog assertion failed (`before.xml`): the original device editor
was a QWidget, not a QDialog. Focused run: 8 passed. Initial broader run: 44 passed,
1 failed because an older selector test still required three workspace buttons.
That assertion was updated to the new two-button requirement and a separate
menu action, preserving its history/job assertions.

Final command:

```powershell
$env:QT_QPA_PLATFORM='windows'
python -m pytest -q tests/designer/test_device_workspace.py tests/designer/test_device_project_open.py tests/designer/test_device_finish_ui.py tests/designer/test_project_configuration_ui.py tests/designer/test_online_run.py tests/ui/page_designer/test_workspace_switch.py tests/ui/page_designer/test_workspace_modes.py tests/ui/page_designer/test_workspace.py --junitxml=build/device-dialog/final.xml
```

Result: **46 passed in 20.27 seconds**, no failures/skips. Includes real QAction
activation, native Qt window flags/modality, close/reopen/Esc, nonduplicated owner,
draft history, invalid-input close, metadata-read close, page preservation and
project replacement. Temporary fixtures expose metadata only, not real hardware.
Dialog content screenshots were captured; QWidget.grab excludes native title-bar
decoration, which is checked by window/dialog assertions rather than those pixels.

Ruff passes for three source files and two test modules. Scoped mypy passes for
the three source files under existing untyped-body exclusions. Diff check passes.
No full repository run, frozen rebuild, human UI or real-device acceptance.
Existing running processes/EXEs do not hot-load these edits. Source Designer must
restart; EXE use requires a fresh build. No commit/push is implied by this report.
