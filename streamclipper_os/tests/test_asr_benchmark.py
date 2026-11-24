import unittest,tempfile
from asr_benchmark import benchmark
class ASRPilotTests(unittest.TestCase):
    def test_duration_limit(self):
        with self.assertRaises(ValueError): benchmark('unused','unused',31)
    def test_missing_model(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): benchmark('unused',d)
