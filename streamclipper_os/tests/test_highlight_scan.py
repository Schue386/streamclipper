import unittest
from highlight_scan import rank
class HighlightTests(unittest.TestCase):
    def test_static(self): self.assertEqual(rank([0]*40,[])['candidates'],[])
    def test_peak(self):
        m=[0]*60; m[20]=0.5
        self.assertEqual(rank(m,[])['candidates'][0]['second'],10.5)
    def test_audio(self):
        a=[0]*60; a[20]=0.5
        self.assertEqual(rank([0]*59,a)['candidates'][0]['second'],10)
