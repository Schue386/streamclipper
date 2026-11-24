import unittest
from pathlib import Path
from unittest.mock import patch
from streamclipper.options import clip_layout

class SafeLayoutTests(unittest.TestCase):
    def test_portrait_preserves_composition(self):
        for layout in ('split','uncut-stack'):
            opts, cam, game, note = clip_layout(Path('unused.mp4'), {'width':1080,'height':1920,'dur':75}, {'layout':layout,'ratio':'9:16'})
            self.assertEqual(opts['layout'],'full')
            self.assertIsNone(cam)
            self.assertIsNone(game)
    @patch('streamclipper.options.classify_layout',return_value=None)
    def test_no_detector_no_guessed_webcam(self, detect):
        opts, cam, game, note = clip_layout(Path('unused.mp4'), {'width':1920,'height':1080,'dur':75}, {'layout':'split','ratio':'9:16'})
        self.assertEqual(opts['layout'],'full')
        self.assertIsNone(cam)
