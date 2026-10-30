"""Subprocess CLI test: temporary loopback port, isolated account, no control."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile

class PreviewTests(unittest.TestCase):
    def test_preview_cli(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'test.emoweb'
            manifest={'format':'emo-web-panel-1','projectId':'demo-project','packageRevision':'preview-test','panelContractVersion':'draft-unverified','rendererRequirements':[],'presentationFile':'presentation.json','assets':[]}
            presentation={'pages':[{'id':'main','title':'Preview','components':[{'id':'title','type':'page_title'}]}]}
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('manifest.json',json.dumps(manifest));z.writestr('presentation.json',json.dumps(presentation))
            process=subprocess.Popen([sys.executable,'-m','emo_web_panel.main','preview','--package',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                line=process.stdout.readline()
                self.assertTrue(line,'Preview must emit connection details')
                ready=json.loads(line);self.assertTrue(ready['url'].startswith('http://127.0.0.1:'));self.assertNotEqual(ready['url'],'http://127.0.0.1:0')
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(ready['url']+'/api/health',timeout=1) as response:health=json.load(response)
                        break
                    except OSError:time.sleep(.05)
                else:self.fail('Preview did not start')
                self.assertEqual(health['mode'],'mock');self.assertFalse(health['productionContractReady'])
            finally:
                process.terminate()
                try:process.communicate(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.communicate()
