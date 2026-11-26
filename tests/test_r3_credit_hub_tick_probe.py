"""No Qt needed: verify original method calls, restore, gaps and evidence bounds."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as S
from scripts.r3_credit_hub_tick_probe import HubTickProbe


class Session:
    def __init__(self, number):
        self.number=number
        self.calls=0
    def readSnapshot(self):
        self.calls+=1
        return S(scopes={"synthetic":S(result=S(identity=S(resultOrdinal=self.number)))})


class ProbeTests(unittest.TestCase):
    def test_observes_only_existing_read_and_restores(self):
        probe=HubTickProbe()
        session=Session(75)
        hub=S(lastToken=None, session=session)
        def tick(h):
            number=h.session.readSnapshot().scopes["synthetic"].result.identity.resultOrdinal
            h.lastToken=(number,)
            return number
        self.assertEqual(probe.wrap(tick)(hub),75)
        self.assertEqual(session.calls,1)
        self.assertNotIn("readSnapshot",vars(session))
        self.assertEqual(probe.rows[0]["reads"][0]["ordinals"],[75])
        self.assertTrue(probe.rows[0]["advanced"])
    
    def test_preserves_original_raise_and_preexisting_override(self):
        probe=HubTickProbe()
        session=Session(75)
        callback=session.readSnapshot
        session.readSnapshot=callback
        hub=S(lastToken=None, session=session)
        def tick(h):
            h.session.readSnapshot()
            raise ValueError("original")
        with self.assertRaisesRegex(ValueError,"original"):
            probe.wrap(tick)(hub)
        self.assertIs(session.readSnapshot,callback)
        self.assertFalse(probe.rows[0]["finished"])
        self.assertEqual(session.calls,1)

    def test_no_read_is_not_invented_and_limits_fail_closed(self):
        probe=HubTickProbe()
        probe.MAX_ROWS=1
        hub=S(lastToken=None, session=Session(75))
        def tick(h): return None
        probe.wrap(tick)(hub)
        probe.wrap(tick)(hub)
        self.assertFalse(probe.rows[0]["reads"])
        self.assertFalse(probe.rows[0]["advanced"])
        self.assertEqual(probe.dropped,1)
        probe.MAX_BYTES=110
        with TemporaryDirectory() as root:
            path=Path(root)/"test.json"
            self.assertTrue(probe.save(path))
            data=json.loads(path.read_bytes())
            self.assertEqual(data["rows"],[])
            self.assertEqual(data["dropped"],2)
            self.assertFalse(probe.save(path))


if __name__ == "__main__":
    unittest.main()
