import subprocess
import unittest
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/'deploy/install.sh'
class InstallerTests(unittest.TestCase):
    def test_syntax(self):
        subprocess.run(['bash','-n',str(SCRIPT)],check=True)
    def test_plan(self):
        result=subprocess.run(['bash',str(SCRIPT),'--plan'],capture_output=True,text=True,check=True)
        self.assertIn('No service start',result.stdout)
    def test_explicit_install_required(self):
        result=subprocess.run(['bash',str(SCRIPT)],capture_output=True)
        self.assertEqual(result.returncode,2)
