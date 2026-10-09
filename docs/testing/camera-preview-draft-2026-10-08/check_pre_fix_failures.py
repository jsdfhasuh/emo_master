"""Replay the two unrelated failures with pre-fix code without rewriting live files."""
import importlib.abc
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

root = Path("C:/Users/jsdfhasuh/my_scripts/emo_master")
baseline = Path(__file__).parent
sys.path.insert(0, str(root / "src"))
modules = {}
for saved in (baseline / "src").rglob("*.py"):
    relative = saved.relative_to(baseline / "src")
    name = ".".join(relative.with_suffix("").parts)
    modules[name] = (saved, root / "src" / relative)


class BaselineLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        saved, original = modules[fullname]
        return compile(saved.read_text(encoding="utf-8"), str(original), "exec")


class BaselineFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in modules:
            saved, original = modules[fullname]
            return importlib.util.spec_from_file_location(fullname, original,
                loader=BaselineLoader(fullname, str(original)))


sys.meta_path.insert(0, BaselineFinder())
print("Pre-fix module overlay count:", len(modules), flush=True)
import pytest
targets = sys.argv[1:] or [
    "tests/designer/test_minimal_project_metadata.py::testEditorUsesLateCatalogWithoutMutatingSavedGraph",
    "tests/designer/test_minimal_project_metadata.py::testNormalizationPreservesSavedContractsAndOwnsFallbackSchema"]
raise SystemExit(pytest.main(["-q", *targets]))
