"""Fully synthetic test data: assert the required shard invariant."""
import json
import unittest

from scripts.check_source_union import check_partition, collections


def mock_member(shard, selected, *, finished=None, exitstatus=0, junit=b"<testsuite><testcase/></testsuite>"):
    return collections({
        "ci-results.xml": junit,
        "ci-shard.json": json.dumps({
            "shard": shard, "exitstatus": exitstatus, "collected": ["a", "b", "c"],
            "selected": selected, "executed": selected if finished is None else finished,
        }),
    }, shard)


class TestPartition(unittest.TestCase):
    def test_exact_complete_union(self):
        self.assertEqual(check_partition(mock_member("runtime", ["a"]), mock_member("other", ["b", "c"])), 3)

    def test_overlap_and_missing_are_not_green(self):
        a = mock_member("runtime", ["a"])
        for other in (["a", "b", "c"], ["b"]):
            with self.subTest(other=other), self.assertRaises(ValueError):
                check_partition(a, mock_member("other", other))

    def test_requires_successful_execution_and_junit(self):
        for args in ({"finished": []}, {"exitstatus": 1},
                     {"junit": b"<testsuite><testcase><failure/></testcase></testsuite>"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                mock_member("runtime", ["a"], **args)
