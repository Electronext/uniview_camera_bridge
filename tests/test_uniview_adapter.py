from __future__ import annotations

import sys, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'uniview_camera_bridge'))
import onvif_adapter

class SharedStub:
    def __init__(self):self.calls=[]
    def continuous_move(self,**kw):self.calls.append(('continuous_move',kw))
    def stop_move(self,**kw):self.calls.append(('stop_move',kw))

class LegacyCamera:
    _shared_onvif_patched=False
    def __init__(self,host,username,password,timeout=15.0):
        self.base_url='http://'+host

class Tests(unittest.TestCase):
    def patched(self):
        class C(LegacyCamera):pass
        onvif_adapter.patch_uniview_camera(C)
        obj=C.__new__(C); obj.onvif=SharedStub()
        return obj,obj.onvif

    def test_pt_only_omits_legacy_zero_zoom(self):
        cam,shared=self.patched()
        cam.continuous_move(.3,0,0)
        self.assertEqual(shared.calls,[('continuous_move',{'pan':.3,'tilt':0.0,'zoom':None,'profile':None})])

    def test_zoom_only_omits_legacy_zero_pt(self):
        cam,shared=self.patched()
        cam.continuous_move(0,0,.4)
        self.assertEqual(shared.calls,[('continuous_move',{'pan':None,'tilt':None,'zoom':.4,'profile':None})])

    def test_all_zero_preserves_legacy_stop(self):
        cam,shared=self.patched()
        cam.continuous_move(0,0,0,'p1')
        self.assertEqual(shared.calls,[('stop_move',{'profile':'p1','pan_tilt':True,'zoom':True})])

if __name__=='__main__':unittest.main()
