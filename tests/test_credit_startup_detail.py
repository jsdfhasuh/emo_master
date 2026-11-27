"""Run the exact startup class without importing runtime/Qt into this fixture."""
import ast
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

FILE=Path(__file__).resolve().parents[1]/"scripts/r3_policy_measure.py"
CLASS=next(x for x in ast.parse(FILE.read_text()).body
           if isinstance(x,ast.ClassDef) and x.name=="StartupReadiness")


class Clock:
    value=0
    def monotonic(self):
        self.value+=1
        return self.value
    def perf_counter_ns(self):return self.value*1_000_000_000
    def sleep(self,_):pass


def fixture():
    env={"json":json,"threading":threading,"time":Clock(),"STARTUP_SECONDS":10,"STARTUP_PROTOCOL":"v1"}
    exec(compile(ast.Module(body=[CLASS],type_ignores=[]),str(FILE),"exec"),env)
    owner=env["StartupReadiness"](observe=True)
    owner.job="fixture"
    owner.inputGate[1].value=1
    owner.paints={0:1,1:2}
    contexts=[object(),object()]
    for c in contexts: owner.observeSubscription(owner.job,c)
    app=SimpleNamespace(processEvents=lambda:None)
    clients=[SimpleNamespace(lock=threading.Lock(),jobId=owner.job,instanceId="runtime",compatible=True,
                    connection="CONNECTED",stream=object(),records=[],errors=[]) for _ in range(2)]
    windows=[SimpleNamespace(isVisible=lambda:True) for _ in range(2)]
    return owner,app,clients,windows


class Detail(unittest.TestCase):
    def test_failure_retains_which_readiness_condition_blocked(self):
        owner,app,clients,windows=fixture()
        clients[1].stream=None
        with self.assertRaises(TimeoutError):owner.release(app,clients,windows,"runtime")
        self.assertFalse(owner.inputGate[0].is_set())
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            owner.saveDiagnostic(root)
            d=json.loads((root/"startup-readiness-detail.json").read_text())
        self.assertEqual(d["last_poll"]["connected"],[True,False])
        self.assertEqual(d["last_poll"]["painted"],2)
        self.assertTrue(d["last_poll"]["subscribed"])
        self.assertIsNone(d["gate_opened_ns"])
        self.assertEqual(d["startup_budget_seconds"],10)
        self.assertIn("owner_release_expired",d["marks_ns"])

    def test_happy_path_gate_same_to_original_motion_boundary(self):
        owner,app,clients,windows=fixture()
        owner.release(app,clients,windows,"runtime")
        self.assertTrue(owner.inputGate[0].is_set())
        self.assertGreater(owner.openedNs,0)
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            owner.saveDiagnostic(root)
            d=json.loads((root/"startup-readiness-detail.json").read_text())
        self.assertEqual(d["last_poll"]["connected"],[True,True])
        self.assertEqual(d["gate_opened_ns"],owner.openedNs)
        self.assertNotIn("owner_release_expired",d["marks_ns"])


if __name__=="__main__": unittest.main()
