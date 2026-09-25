import sys, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'uniview_camera_bridge'))
from onvif_adapter import patch_uniview_camera

class Legacy:
    def __init__(self,host,user,password,timeout=15):self.base_url='http://'+host
class Fake:
    def __init__(self,*args,**kwargs):self.kwargs=kwargs
    def stop_move(self,**kwargs):pass

class Tests(unittest.TestCase):
    def test_uniview_prefers_legacy_getcapabilities_order(self):
        with patch('onvif_adapter.ONVIFCamera',Fake):
            patch_uniview_camera(Legacy); c=Legacy('192.168.90.5:30020','admin','secret'); self.assertFalse(c.onvif.kwargs['prefer_getservices'])

if __name__=='__main__':unittest.main()
