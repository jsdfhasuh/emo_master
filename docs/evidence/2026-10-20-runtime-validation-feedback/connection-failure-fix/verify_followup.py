"""Repeat the affected checks without overwriting the original 529-test evidence."""
import importlib.util
from pathlib import Path


def main():
    output = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('runtime_feedback_checks', output.parent / 'verify_software.py')
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    checks.OUTPUT = output
    return checks.main()


if __name__ == '__main__':
    raise SystemExit(main())
