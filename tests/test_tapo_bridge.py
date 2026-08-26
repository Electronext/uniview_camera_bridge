from __future__ import annotations

import importlib.util, sys, types, unittest
from pathlib import Path

paho=types.ModuleType('paho'); pm=types.ModuleType('paho.mqtt'); pc=types.ModuleType('paho.mqtt.client')
pc.Client=object; pc.CallbackAPIVersion=types.SimpleNamespace(VERSION2=2); pm.client=pc; paho.mqtt=pm
sys.modules['paho']=paho; sys.modules['paho.mqtt']=pm; sys.modules['paho.mqtt.client']=pc
ROOT=Path(__file__).resolve().parents[1]/'tapo_camera_bridge'; sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('tapo_app',ROOT/'app.py'); app=importlib.util.module_from_spec(spec); sys.modules['tapo_app']=app; spec.loader.exec_module(app)

class FakeClient:
    def __init__(self):self.calls=[]
    def continuous_move(self,**kw):self.calls.append(('continuous_move',kw))
    def stop_move(self,**kw):self.calls.append(('stop_move',kw))
    def absolute_move(self,**kw):self.calls.append(('absolute_move',kw))
    def relative_move(self,**kw):self.calls.append(('relative_move',kw))
    def goto_preset(self,v):self.calls.append(('goto_preset',v))

class Tests(unittest.TestCase):
    def runtime(self):
        c=FakeClient(); r=app.CameraRuntime('c220','Indoor PTZ',c,{}, {'pan_tilt_absolute':True,'pan_tilt_relative':True,'pan_tilt_continuous':True,'zoom_absolute':False,'zoom_relative':False,'zoom_continuous':False},[]); return r,c
    def test_webrtc_velocity_and_stop(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3}); b.execute(r,'ptz',{'pan':.4,'tilt':-.2}); self.assertEqual(c.calls[0][0],'continuous_move'); b.execute(r,'ptz',{'stop':True}); self.assertEqual(c.calls[1],('stop_move',{'pan_tilt':True,'zoom':False}))
    def test_absolute_and_relative(self):
        r,c=self.runtime(); b=app.Bridge({}); b.execute(r,'absolute',{'pan':.2,'tilt':.58,'speed':.2}); b.execute(r,'relative',{'pan':.05,'tilt':0,'speed':.2}); self.assertEqual([x[0] for x in c.calls],['absolute_move','relative_move'])

if __name__=='__main__':unittest.main()
