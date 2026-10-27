# Single-source application version

Date: 2026-10-26 (Asia/Shanghai).

## Scope and source identity

- Application base: `633de0e197f5553168a7786440199f68f3410bed`, plus local changes.
- Packager base: `b6becdbce9cbfef4daced087df2d8ad7e565b28a`, plus this change.
- The existing unrelated application worktree changes were preserved.
- This changes version maintenance, not the software version. It remains `0.6.1`.
- Project schema versions and field projects were not changed.

## Implementation

- `src/emo_master/app_version.py` is the only application version definition:
  `APP_VERSION = "0.6.1"`.
- `emo_master.__version__` re-exports that constant. Native-library initialization
  remains unchanged.
- Setuptools reads `emo_master.app_version.APP_VERSION` for dynamic package metadata.
- The frozen-package validation workflow reads the version file with AST parsing,
  without importing the application.
- The local packager's existing version reader follows the relative version
  re-export using AST parsing. Both target configurations remain unchanged, and
  historical sources with a literal `__version__` still work.
- The PowerShell publisher uses that same reader and still rejects mismatched tags.

## Final verification

- Python 3.10.21: `pytest -q tests/test_package_bootstrap.py tests/test_app_version.py
  tests/test_release_workflow_contract.py tests/test_windows_package_entry.py
  tests/test_dev_launcher.py`: 17 passed, 2 subtests passed.
- Metadata regression generates actual setuptools metadata from a temporary
  project with version `8.7.6` and an application initializer that raises if
  imported. The generated metadata and CI version command both report `8.7.6`.
- Ruff on changed application Python files and the release workflow contract test:
  passed. Mypy on `__init__.py` and `app_version.py`: passed.
- `python scripts/dev.py run-designer --local --check`: passed; no window or Job
  was started.
- Both actual local packager target configurations read `0.6.1` from this checkout.
- Designer and Runtime `build.py --config configs/<target>.json --dry-run`: passed.
- Packager `python -m unittest discover -s tests -v`: 408 tests, 407 passed,
  1 skipped because directory symlinks were unavailable. Duration: 412.293 seconds.
  The run used an isolated temporary Python 3.10 environment with existing source
  environment packages, Pillow 12.3.0, and `PYTHONUTF8=1`.
- Scoped `git diff --check` passed in both repositories.

## Earlier failures retained

- The initial PowerShell test harness lost the publisher's script directory when
  recreating the function with `Invoke-Expression`, producing
  `ModuleNotFoundError: release_wizard_common` in both version-format cases.
  It now invokes the parsed function body's script block, preserving its source
  location. A subsequent harness-only exit-status failure was fixed by explicitly
  reporting successful completion after catching the expected tag mismatch.
- Initial Ruff reported F401 for the renamed version import. An explicit
  `__all__ = ["__version__"]` documents the public re-export and resolved it.
- The first packager full-suite attempt used the existing Python 3.12 environment,
  which lacked PyYAML and Pillow: 394 tests reported, 51 failures, 71 errors,
  1 skipped. Missing imports also prevented some tests from loading. This is a
  failed attempt, not a pass. The final run used the temporary environment above;
  no dependency was installed into the application's existing environment.

## Cloud rollout and unperformed checks

- Cloud rollout is pending. The application release and frozen-validation callers
  still pin packager `d5f3617a7265c545c793366f93e3a114212d8c59`. Inspection of that
  exact commit confirmed its publisher only matches literal `__version__` text.
- Before publishing the application migration, commit and push the companion
  packager changes, then update the Windows callers to that reviewed fixed commit.
  Keep reusable-workflow refs and `packager_ref` selections consistent; include
  the Operator Runtime workflow and its explicit packager input.
- No commit, push, tag, GitHub Actions dispatch, or Release publication was performed.
- No product EXE build, frozen acceptance, manual GUI acceptance, or device test was
  performed. Dry-run and unit-test results do not establish those outcomes.

## Subsequent v0.6.2 release preparation

- The user subsequently authorized changing only `APP_VERSION` to `0.6.2` and
  committing the version migration separately from unrelated workspace changes.
- Packager `3ef28d7f581dfff09fc900ba64df34b275cc1dcd` includes the shared version
  reader, independent Runtime installer and combined six-asset release workflow.
- The source release caller now delegates to that pinned combined workflow;
  frozen-package validation and standalone Runtime callers use the same packager.
- Release notes reuse the training platform's title, summary, update/fix groups,
  download section, source comparison and full commit history.
- The earlier results above are retained. Cloud product acceptance and actual
  publication are separate subsequent checks, not implied by this preparation.
