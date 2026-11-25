import unittest
from clip_preview import plan,captions_srt
from highlight_scan import rank
class PreviewTests(unittest.TestCase):
    def test_selection(self): self.assertEqual(plan(180,[{'second':100,'score':.5}])['start'],80)
    def test_short(self): self.assertEqual(plan(35,[])['duration'],35)
    def test_empty(self): self.assertFalse(plan(180,[])['publishable'])
    def test_captions(self):
        self.assertEqual(captions_srt([{'start':1,'end':2,'text':'.'}],75),'')
        self.assertIn('00:00:01,000 --> 00:00:02,000',captions_srt([{'start':1,'end':2,'text':'Hello'}],75))
    def test_adaptive(self):
        self.assertTrue(rank([0]*20+[.01]+[0]*20,[])['candidates'])
