import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.traits import load_traits
from skills.constants import TRAITS_ROOT

class TestTraits(unittest.TestCase):
    def setUp(self):
        # Create a dummy trait file for testing
        TRAITS_ROOT.mkdir(parents=True, exist_ok=True)
        self.dummy_trait_path = TRAITS_ROOT / "test_dummy.md"
        self.dummy_trait_path.write_text("dummy content", encoding="utf-8")

    def tearDown(self):
        if self.dummy_trait_path.exists():
            self.dummy_trait_path.unlink()

    def test_load_traits(self):
        traits = ["test_dummy", "non_existent"]
        result = load_traits(traits)
        
        self.assertIn("【动态挂载特征能力】", result)
        self.assertIn("=== [特征能力: test_dummy] ===", result)
        self.assertIn("dummy content", result)
        self.assertNotIn("non_existent", result) # Should gracefully ignore missing

    def test_empty_traits(self):
        self.assertEqual(load_traits([]), "")
        self.assertEqual(load_traits(None), "")

if __name__ == "__main__":
    unittest.main()
