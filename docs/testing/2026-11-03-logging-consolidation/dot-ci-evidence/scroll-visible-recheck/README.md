# Restored visible scroll regression: 2026-11-04 00:27–00:29 UTC

Actual cloud desktop remains the same Linux/Xfce session with original formal Designer/Operator windows. Verified original terminal contents and window IDs through official CUA APIs; no user computer or Sky API was used. Exec process namespace is separate and its empty ps does not indicate the visible apps ended.

Source: base 9079e444c3aeb7ab7f5aa645c9c055b418b2ba1b plus uncommitted log_dialog.py patch SHA256 06eace5e07d42b47983bb57f73cd3650ab02b1218e3008097e08ec5701e8fc49. Formal Designer `scripts/dev.py run-designer`, existing isolated settings-designer-fixed; current data root designer-retry-data, no devices. Remote branch independently verified at base SHA before work.

Visible steps, all real mouse/keyboard on fixed Designer log window 50331723:
1. Clear pre-existing job.completed search. 40/40 records. Checked follow displays the actual newest rows at23:46:09, scrollbar thumb at bottom. Evidence01.
2. Uncheck automatic scrolling. Select table and Ctrl+Home. Actual viewport shows first timestamp23:42:10.432. Evidence02.
3. Click formal main-window Run button for the existing device-free SDK project while logs remain open. Appends increase count40→42→53. With follow off, selected first row and viewport remain23:42:10.432. Evidence03 records53 after append.
4. Re-enable follow. Actual visible table moves to newest completed Job rows00:28:36–00:28:37 with53 records. Evidence04. This is not merely scrollbar state.
5. Escape closes the log window; official CUA reports the bound window is not mapped. Ctrl+Shift+L from formal Designer reopens same log window, visible tail remains newest at53 records. Evidence05.

Screenshots01–05 in this directory are direct official CUA screenshot bytes. No QTest, probe, offscreen render, private controller invocation or fabricated screenshot was used. Full eight-group handoff acceptance remains in progress separately.
