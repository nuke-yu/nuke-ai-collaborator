import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestIpcTypes(unittest.TestCase):
    def test_query_and_mutate_are_downstream(self):
        from runtime.ipc import protocol
        self.assertEqual(protocol.QUERY, "query")
        self.assertEqual(protocol.MUTATE, "mutate")
        self.assertIn(protocol.QUERY, protocol.DOWNSTREAM)
        self.assertIn(protocol.MUTATE, protocol.DOWNSTREAM)


if __name__ == "__main__":
    unittest.main()
