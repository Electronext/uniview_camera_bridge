import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "uniview_camera_bridge"))
from onvif_adapter import patch_uniview_camera


class LegacyCamera:
    def __init__(self, host, username, password, timeout=15.0):
        self.base_url = "http://" + host
        self.legacy = True


class FakeONVIF:
    def __init__(self, *args, **kwargs): self.calls=[]
    def get_ptz_configuration_options(self, p=None): self.calls.append(("opts",p)); return {"spaces":{}}
    def get_ptz_node_capabilities(self): return {"maximum_presets":8}
    def get_zoom(self,p=None): return .5
    def set_zoom(self,t,p=None): self.calls.append(("set",t,p))
    def continuous_move(self,**kw): self.calls.append(("move",kw))
    def stop_move(self,**kw): self.calls.append(("stop",kw))


class AdapterTests(unittest.TestCase):
    def test_patch_preserves_init_and_public_ptz_api(self):
        with patch("onvif_adapter.ONVIFCamera", FakeONVIF):
            patch_uniview_camera(LegacyCamera)
            c=LegacyCamera("192.168.90.5:30020","admin","secret")
            self.assertTrue(c.legacy)
            self.assertEqual(c.get_zoom(),.5)
            c.continuous_move(pan=.2,tilt=-.1)
            c.stop_move()
            self.assertEqual(c.onvif.calls[-1],("stop",{"profile":None,"pan_tilt":True,"zoom":True}))

if __name__ == "__main__": unittest.main()
