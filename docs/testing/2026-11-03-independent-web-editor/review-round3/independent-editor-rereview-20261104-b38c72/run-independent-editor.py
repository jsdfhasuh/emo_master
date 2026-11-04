import sys
from pathlib import Path
repo = Path(r'D:\jsdfhasuh\documents\my_project\emo_master')
evidence = Path(__file__).resolve().parent
sys.path.insert(0, str(repo / 'web_panel/frontend/tests'))
import editor_round3_browser as suite
suite.EVIDENCE = evidence
suite.f.ROOT = evidence
sys.argv = [suite.__file__, '--label', 'independent-current']
raise SystemExit(suite.main())
