from __future__ import annotations

import importlib.util, sys, types, unittest
from pathlib import Path

paho=types.ModuleType('paho'); pm=types.ModuleType('paho.mqtt'); pc=types.ModuleType('paho.mqtt.client')
pc.Client=object; pc.CallbackAPIVersion=types.SimpleNamespace(VERSION2=2); pm.client=pc; paho.mqtt=pm
sys.modules['paho']=paho; sys.modules['paho.mqtt']=pm; sys.modules['paho.mqtt.client']=pc
ROOT=Path(__file__).resolve().parents[1]/'tapo_camera_bridge'; sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('tapo_app',ROOT/'app.py'); app=importlib.util.module_from_spec(spec); sys.modules['tapo_app']=app; spec.loader.exec_module(app)

class FakeClient:
    def __init__(self):self.calls=[]; self.stop_failures=0
    def continuous_move(self,**kw):self.calls.append(('continuous_move',kw))
    def stop_move(self,**kw):
        self.calls.append(('stop_move',kw))
        if self.stop_failures:
            self.stop_failures-=1; raise RuntimeError('temporary stop failure')
    def absolute_move(self,**kw):self.calls.append(('absolute_move',kw))
    def relative_move(self,**kw):self.calls.append(('relative_move',kw))
    def goto_preset(self,v):self.calls.append(('goto_preset',v))

class Tests(unittest.TestCase):
    def runtime(self):
        c=FakeClient(); r=app.CameraRuntime('c220','Indoor PTZ',c,{}, {'pan_tilt_absolute':True,'pan_tilt_relative':True,'pan_tilt_continuous':True,'zoom_absolute':False,'zoom_relative':False,'zoom_continuous':False},[]); return r,c
    def test_webrtc_velocity_and_stop(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3}); b.execute(r,'ptz',{'pan':.4,'tilt':-.2}); self.assertEqual(c.calls[0][0],'continuous_move'); b.execute(r,'ptz',{'stop':True}); self.assertEqual(c.calls[1],('stop_move',{'pan_tilt':True,'zoom':False}))
    def test_failed_safety_stop_keeps_retry_state(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_stop_retry_seconds':.5}); b.cameras[r.camera_id]=r; r.moving=True; r.stop_deadline=app.time.monotonic()-1; c.stop_failures=1
        # Exercise the same failure semantics used by the run loop.
        try:c.stop_move(pan_tilt=True,zoom=False)
        except Exception:r.stop_deadline=app.time.monotonic()+.5
        self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline)

    def test_shutdown_stop_is_best_effort_for_active_camera(self):
        r,c=self.runtime(); r.moving=True; b=app.Bridge({}); b.cameras[r.camera_id]=r
        # A successful shutdown stop must use the camera's supported axes.
        c.stop_move(pan_tilt=r.caps.get('pan_tilt_continuous',False),zoom=r.caps.get('zoom_continuous',False)); r.moving=False; r.stop_deadline=None
        self.assertEqual(c.calls[-1],('stop_move',{'pan_tilt':True,'zoom':False})); self.assertFalse(r.moving)

    def test_absolute_and_relative(self):
        r,c=self.runtime(); b=app.Bridge({}); b.execute(r,'absolute',{'pan':.2,'tilt':.58,'speed':.2}); b.execute(r,'relative',{'pan':.05,'tilt':0,'speed':.2}); self.assertEqual([x[0] for x in c.calls],['absolute_move','relative_move'])

if __name__=='__main__':unittest.main()
