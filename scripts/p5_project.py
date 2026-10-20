"""Source launcher usable from a non-project cwd."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from emo_master.apps.delivery.main import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
