from __future__ import annotations

import importlib.util, sys, threading, time, types, unittest
from pathlib import Path

paho=types.ModuleType('paho'); pm=types.ModuleType('paho.mqtt'); pc=types.ModuleType('paho.mqtt.client')
pc.Client=object; pc.CallbackAPIVersion=types.SimpleNamespace(VERSION2=2); pm.client=pc; paho.mqtt=pm
sys.modules['paho']=paho; sys.modules['paho.mqtt']=pm; sys.modules['paho.mqtt.client']=pc
ROOT=Path(__file__).resolve().parents[1]/'tapo_camera_bridge'; sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('tapo_app',ROOT/'app.py'); app=importlib.util.module_from_spec(spec); sys.modules['tapo_app']=app; spec.loader.exec_module(app)

class FakeClient:
    def __init__(self):self.calls=[]; self.stop_failures=0; self.move_failures=0; self.move_block=None; self.stop_seen=threading.Event()
    def continuous_move(self,**kw):
        self.calls.append(('continuous_move',kw))
        if self.move_failures:
            self.move_failures-=1; raise RuntimeError('ambiguous movement failure')
        if self.move_block:self.move_block.wait(2)
    def stop_move(self,**kw):
        self.calls.append(('stop_move',kw)); self.stop_seen.set()
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
    def test_ambiguous_continuous_move_failure_remains_armed_for_stop(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3}); c.move_failures=1
        with self.assertRaises(RuntimeError):b.execute(r,'ptz',{'pan':.4,'tilt':0})
        self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline)

    def test_failed_safety_stop_keeps_retry_state(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_stop_retry_seconds':.5}); b.cameras[r.camera_id]=r; b.arm_movement(r); r.stop_deadline=app.time.monotonic()-1; c.stop_failures=1
        self.assertFalse(b.safety_stop_once(r))
        self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline); self.assertFalse(r.stop_in_progress)

    def test_watchdog_stops_while_continuous_move_request_is_blocked(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':.08}); b.cameras[r.camera_id]=r
        c.move_block=threading.Event(); b.start_watchdog()
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.4,'tilt':0}),daemon=True); worker.start()
        try:
            self.assertTrue(c.stop_seen.wait(.6),'watchdog did not issue Stop while ContinuousMove was blocked')
            self.assertTrue(worker.is_alive(),'ContinuousMove request should still be blocked when Stop is issued')
        finally:
            c.move_block.set(); worker.join(1); b.stop_watchdog()
        self.assertFalse(r.moving); self.assertIsNone(r.stop_deadline)

    def test_newer_movement_generation_is_not_cleared_by_older_stop(self):
        r,c=self.runtime(); b=app.Bridge({}); b.cameras[r.camera_id]=r; first=b.arm_movement(r)
        with r.state_lock:r.stop_in_progress=True
        b.arm_movement(r)
        with r.state_lock:
            if r.movement_generation==first:r.moving=False; r.stop_deadline=None
            r.stop_in_progress=False
        self.assertTrue(r.moving); self.assertGreater(r.movement_generation,first)

    def test_absolute_and_relative(self):
        r,c=self.runtime(); b=app.Bridge({}); b.execute(r,'absolute',{'pan':.2,'tilt':.58,'speed':.2}); b.execute(r,'relative',{'pan':.05,'tilt':0,'speed':.2}); self.assertEqual([x[0] for x in c.calls],['absolute_move','relative_move'])

if __name__=='__main__':unittest.main()
