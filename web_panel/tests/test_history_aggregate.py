import unittest
from emo_web_panel.mock_runtime import MockRuntime

class HistoryAggregateTests(unittest.IsolatedAsyncioTestCase):
    async def test_declared_aggregate_and_filter_policy(self):
        rt=MockRuntime()
        for operation,expected in [('count',30),('sum',465),('avg',15.5),('min',1),('max',30)]:
            result=await rt.history('history-count',1,10,q='NG',aggregation=operation)
            self.assertEqual(result['value'],expected)
        filtered=await rt.history('history-count',1,10,q='NG',aggregation='sum',follow_filter=True)
        self.assertEqual(filtered['value'],112)
        with self.assertRaises(ValueError):await rt.history('history-count',1,10,field='arbitrary_sql')
