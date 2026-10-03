import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from types import SimpleNamespace
from app.core.config import settings
from app.services.model_worker import needs_retrain


def _run(n=1500, pos=400, d=None):
    return SimpleNamespace(n_samples=n, n_positive=pos, capacity_d=settings.CAPACITY_D if d is None else d)


class TestNeedsRetrain(unittest.TestCase):
    def test_no_run_yet(self):
        self.assertTrue(needs_retrain(None, 10, 3))

    def test_unchanged_labeled_set(self):
        self.assertFalse(needs_retrain(_run(), 1500, 400))

    def test_new_labels(self):
        self.assertTrue(needs_retrain(_run(), 1501, 400))
        self.assertTrue(needs_retrain(_run(), 1500, 401))

    def test_run_from_older_formula(self):
        self.assertTrue(needs_retrain(_run(d=28), 1500, 400))

    def test_run_without_artifact(self):
        self.assertTrue(needs_retrain(_run(), 1500, 400, has_artifact=False))


if __name__ == "__main__":
    unittest.main()
