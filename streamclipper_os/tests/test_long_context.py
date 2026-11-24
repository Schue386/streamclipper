import unittest
from streamclipper.story import story_window
class LongContextTests(unittest.TestCase):
    def test_early_middle_late(self):
        for peak in (1, 40, 100, 179):
            w=story_window(peak,[],[],[],61,90,75,180)
            self.assertAlmostEqual(w['end']-w['start'],75)
            self.assertLessEqual(w['start'],peak)
            self.assertGreaterEqual(w['end'],peak)
            self.assertGreaterEqual(w['start'],0)
            self.assertLessEqual(w['end'],180)
    def test_short_source_not_padded(self):
        w=story_window(20,[],[],[],61,90,75,35)
        self.assertLess(w['end']-w['start'],61)
    def test_legacy(self):
        w=story_window(100,[],[],[],15,60,30,180)
        self.assertLessEqual(w['end']-w['start'],60)
