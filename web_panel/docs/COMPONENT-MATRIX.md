# Renderer matrix — local draft only

All properties are allowlisted by component type; text and icons render as text, not HTML. The internal manifest must say `panelContractVersion: draft-unverified`. This is **not** the missing official published-presentation schema.

| Type | Implemented draft behavior | Evidence / external boundary |
|---|---|---|
| image | bound result bytes staged with values; same-origin URL, owner key, size/SHA256 checks; static package assets; declared fixed mock history image; shared row-selection image; ratio/failure/empty state | Browser + helper tests; real resources/lease/history authorization depends on SDK |
| text | static/bound text, field lookup, unavailable state, safe style | Renderer tests, escaped script text browser check |
| number | bound/static numbers, decimals, unit, null/unavailable | Renderer tests; no inferred result on missing source |
| indicator | explicit bool/string mapping; unknown separated from OK/NG | Renderer tests; never derives verdict from task completion |
| runtime_status | connection vs selected run state | Runtime status is separate from business verdict |
| table | single-result column projection/pagination; resets on result key | Draft data model; authoritative real result identity blocked |
| navigation_button | navigate/freeze/resume_live; published target checking; plain text icon | Browser navigation and freeze checks |
| container | recursive child identity, grid/spacing, label/style | Renderer tests; formal mobile overrides rejected pending schema |
| page_title | selected published page title | Renderer/browser tests |
| page_navigation | published page list/current state | Renderer/browser tests |
| clock | browser/IANA timezone; date/time/datetime; explicitly not record time | Renderer tests; Python tzdata validates package timezone cross-platform |
| sqlite_table | declared columns, pagination/filter/sort/refresh, row selection; query change clears selection/images | Real HTTP with mock history browser checks; real QueryHistoryPage blocked |
| sqlite_value | declared count/sum/avg/min/max and explicit followsFilter policy; null not fake count | Backend aggregate tests; real QueryHistoryAggregate blocked |

Runtime-owned mock counters and event sequences are shown separately. They are not calculated from browser frame counts. The UI bounds retained event rows and resets cursor after instance/cursor rollback. Mock examples do not establish real production continuity or Dispatch semantics.
