from __future__ import annotations

import importlib.util, sys, threading, time, types, unittest
from pathlib import Path

paho=types.ModuleType('paho'); pm=types.ModuleType('paho.mqtt'); pc=types.ModuleType('paho.mqtt.client')
pc.Client=object; pc.CallbackAPIVersion=types.SimpleNamespace(VERSION2=2); pm.client=pc; paho.mqtt=pm
sys.modules['paho']=paho; sys.modules['paho.mqtt']=pm; sys.modules['paho.mqtt.client']=pc
ROOT=Path(__file__).resolve().parents[1]/'tapo_camera_bridge'; sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('tapo_app',ROOT/'app.py'); app=importlib.util.module_from_spec(spec); sys.modules['tapo_app']=app; spec.loader.exec_module(app)

class FakeClient:
    def __init__(self):self.calls=[]; self.stop_failures=0; self.move_failures=0; self.move_block=None; self.stop_block=None; self.stop_seen=threading.Event(); self.target_failures={}; self.target_blocks={}; self.target_seen={}
    def continuous_move(self,**kw):
        self.calls.append(('continuous_move',kw))
        if self.move_block:self.move_block.wait(2)
        if self.move_failures:
            self.move_failures-=1; raise RuntimeError('ambiguous movement failure')
    def stop_move(self,**kw):
        self.calls.append(('stop_move',kw)); self.stop_seen.set()
        if self.stop_block:self.stop_block.wait(2)
        if self.stop_failures:
            self.stop_failures-=1; raise RuntimeError('temporary stop failure')
    def _target(self,name,value):
        self.calls.append((name,value))
        self.target_seen.setdefault(name,threading.Event()).set()
        if self.target_blocks.get(name):self.target_blocks[name].wait(2)
        if self.target_failures.get(name,0):
            self.target_failures[name]-=1; raise RuntimeError('ambiguous target failure')
    def absolute_move(self,**kw):self._target('absolute_move',kw)
    def relative_move(self,**kw):self._target('relative_move',kw)
    def goto_preset(self,v):self._target('goto_preset',v)

class Tests(unittest.TestCase):
    def runtime(self):
        c=FakeClient(); r=app.CameraRuntime('c220','Indoor PTZ',c,{}, {'pan_tilt_absolute':True,'pan_tilt_relative':True,'pan_tilt_continuous':True,'zoom_absolute':False,'zoom_relative':False,'zoom_continuous':False},[]); return r,c
    def test_webrtc_velocity_and_stop(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3}); b.execute(r,'ptz',{'pan':.4,'tilt':-.2}); self.assertEqual(c.calls[0][0],'continuous_move'); b.execute(r,'ptz',{'stop':True}); self.assertEqual(c.calls[1],('stop_move',{'pan_tilt':True,'zoom':False}))
    def test_ambiguous_continuous_move_failure_remains_armed_for_stop(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3}); c.move_failures=1
        with self.assertRaises(RuntimeError):b.execute(r,'ptz',{'pan':.4,'tilt':0})
        self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline)

    def test_stale_watchdog_claim_cannot_stop_new_generation(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.arm_movement(r)
        with r.stop_condition:
            old_generation=r.movement_generation; old_deadline=r.stop_deadline
        b.arm_movement(r)
        self.assertFalse(b.safety_stop_once(r,old_generation,old_deadline))
        self.assertFalse(any(name=='stop_move' for name,_ in c.calls))
        self.assertTrue(r.moving); self.assertGreater(r.movement_generation,old_generation)

    def test_stale_watchdog_deadline_cannot_claim_same_generation(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.arm_movement(r)
        with r.stop_condition:
            generation=r.movement_generation; old_deadline=r.stop_deadline
            r.stop_deadline_pt=old_deadline+1; b.sync_moving(r)
        self.assertFalse(b.safety_stop_once(r,generation,old_deadline))
        self.assertFalse(any(name=='stop_move' for name,_ in c.calls))

    def test_failed_safety_stop_keeps_retry_state(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_stop_retry_seconds':.5}); b.cameras[r.camera_id]=r; b.arm_movement(r); r.stop_deadline_pt=app.time.monotonic()-1; b.sync_moving(r); c.stop_failures=1
        self.assertFalse(b.safety_stop_once(r))
        self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline); self.assertFalse(r.stop_in_progress)

    def test_watchdog_stops_while_continuous_move_request_is_blocked(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':.08}); b.cameras[r.camera_id]=r
        c.move_block=threading.Event(); b.start_watchdog()
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.4,'tilt':0}),daemon=True); worker.start()
        try:
            self.assertTrue(c.stop_seen.wait(.6),'watchdog did not issue Stop while ContinuousMove was blocked')
            self.assertTrue(worker.is_alive(),'ContinuousMove request should still be blocked when Stop is issued')
            # Releasing a ContinuousMove after its first safety Stop can
            # restart motion; wait for the required follow-up Stop.
            c.stop_seen.clear()
            c.move_block.set(); worker.join(1)
            self.assertTrue(c.stop_seen.wait(.5),'late successful move was not followed by Stop')
        finally:
            c.move_block.set(); b.stop_watchdog()
        self.assertFalse(r.moving); self.assertIsNone(r.stop_deadline)

    def test_late_continuous_move_is_stopped_again(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':.05}); b.cameras[r.camera_id]=r
        c.move_block=threading.Event(); b.start_watchdog()
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.4,'tilt':0}),daemon=True); worker.start()
        try:
            self.assertTrue(c.stop_seen.wait(.5),'initial watchdog Stop missing')
            c.move_block.set(); worker.join(1)
            self.assertFalse(worker.is_alive()); self.assertTrue(r.moving)
            c.stop_seen.clear()
            self.assertTrue(c.stop_seen.wait(.5),'late ContinuousMove was not stopped again')
        finally:
            c.move_block.set(); b.stop_watchdog()
        self.assertFalse(r.moving)

    def test_target_request_keeps_safety_armed_while_blocked(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3,'ptz_transition_safety_seconds':.05}); b.cameras[r.camera_id]=r
        b.execute(r,'ptz',{'pan':.4,'tilt':0})
        c.target_blocks['absolute_move']=threading.Event(); b.start_watchdog()
        worker=threading.Thread(target=lambda:b.execute(r,'absolute',{'pan':.2,'tilt':.3}),daemon=True); worker.start()
        try:
            for _ in range(50):
                if c.target_seen.get('absolute_move') and c.target_seen['absolute_move'].is_set():break
                time.sleep(.01)
            self.assertTrue(c.target_seen['absolute_move'].is_set())
            self.assertTrue(c.stop_seen.wait(.5),'blocked target left old ContinuousMove unprotected')
            self.assertTrue(worker.is_alive())
        finally:
            c.target_blocks['absolute_move'].set(); worker.join(1); b.stop_watchdog()
        self.assertFalse(r.moving); self.assertIsNone(r.stop_deadline)

    def test_failed_late_continuous_move_is_stopped_again(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':.05}); b.cameras[r.camera_id]=r
        c.move_block=threading.Event(); c.move_failures=1; b.start_watchdog()
        error=[]
        def move():
            try:b.execute(r,'ptz',{'pan':.4,'tilt':0})
            except Exception as e:error.append(e)
        worker=threading.Thread(target=move,daemon=True); worker.start()
        try:
            self.assertTrue(c.stop_seen.wait(.5),'initial watchdog Stop missing')
            c.move_block.set(); worker.join(1)
            self.assertTrue(error); self.assertTrue(r.moving)
            c.stop_seen.clear()
            self.assertTrue(c.stop_seen.wait(.5),'ambiguous failed late ContinuousMove was not stopped again')
        finally:
            c.move_block.set(); b.stop_watchdog()
        self.assertFalse(r.moving)

    def test_continuous_outcome_during_inflight_stop_requires_followup(self):
        r,c=self.runtime(); safety=FakeClient(); r.safety_client=safety
        b=app.Bridge({'ptz_safety_timeout_seconds':.05}); b.cameras[r.camera_id]=r
        c.move_block=threading.Event(); safety.stop_block=threading.Event(); b.start_watchdog()
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.4,'tilt':0}),daemon=True); worker.start()
        try:
            self.assertTrue(safety.stop_seen.wait(.5),'first safety Stop did not start')
            c.move_block.set(); worker.join(1)
            self.assertFalse(worker.is_alive())
            with r.stop_condition:self.assertEqual(r.stop_again_generation,r.movement_generation)
            safety.stop_seen.clear(); safety.stop_block.set()
            self.assertTrue(safety.stop_seen.wait(.5),'follow-up Stop after in-flight overlap missing')
        finally:
            c.move_block.set(); safety.stop_block.set(); b.stop_watchdog()
        names=[name for name,_ in safety.calls]
        self.assertGreaterEqual(names.count('stop_move'),2)
        self.assertFalse(r.moving)

    def test_failed_continuous_outcome_during_inflight_stop_requires_followup(self):
        r,c=self.runtime(); safety=FakeClient(); r.safety_client=safety
        b=app.Bridge({'ptz_safety_timeout_seconds':.05}); b.cameras[r.camera_id]=r
        c.move_block=threading.Event(); c.move_failures=1; safety.stop_block=threading.Event(); b.start_watchdog()
        error=[]
        def move():
            try:b.execute(r,'ptz',{'pan':.4,'tilt':0})
            except Exception as e:error.append(e)
        worker=threading.Thread(target=move,daemon=True); worker.start()
        try:
            self.assertTrue(safety.stop_seen.wait(.5))
            c.move_block.set(); worker.join(1)
            self.assertTrue(error)
            with r.stop_condition:self.assertEqual(r.stop_again_generation,r.movement_generation)
            safety.stop_seen.clear(); safety.stop_block.set()
            self.assertTrue(safety.stop_seen.wait(.5),'failed late move did not cause post-flight Stop')
        finally:
            c.move_block.set(); safety.stop_block.set(); b.stop_watchdog()
        self.assertGreaterEqual(sum(1 for name,_ in safety.calls if name=='stop_move'),2)
        self.assertFalse(r.moving)

    def test_blocked_stop_for_one_camera_does_not_starve_another(self):
        r1,c1=self.runtime(); r1.camera_id='cam1'; r1.name='Camera 1'
        r2,c2=self.runtime(); r2.camera_id='cam2'; r2.name='Camera 2'
        b=app.Bridge({'ptz_safety_timeout_seconds':.05}); b.cameras={'cam1':r1,'cam2':r2}
        c1.stop_block=threading.Event(); b.arm_movement(r1); b.arm_movement(r2)
        with r1.stop_condition:r1.stop_deadline_pt=time.monotonic()-.01; b.sync_moving(r1)
        with r2.stop_condition:r2.stop_deadline_pt=time.monotonic()-.01; b.sync_moving(r2)
        b.start_watchdog()
        try:
            self.assertTrue(c1.stop_seen.wait(.4),'camera 1 Stop did not start')
            self.assertTrue(c2.stop_seen.wait(.4),'camera 2 Stop was starved by camera 1')
        finally:
            c1.stop_block.set(); b.stop_watchdog()
        self.assertFalse(r2.moving)

    def test_new_movement_waits_for_inflight_safety_stop(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':.05}); b.cameras[r.camera_id]=r
        c.stop_block=threading.Event(); b.arm_movement(r)
        with r.stop_condition:r.stop_deadline_pt=time.monotonic()-.01; b.sync_moving(r)
        b.start_watchdog()
        self.assertTrue(c.stop_seen.wait(.4),'safety Stop did not start')
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.7,'tilt':0}),daemon=True); worker.start()
        time.sleep(.08)
        self.assertFalse(any(name=='continuous_move' for name,_ in c.calls),'new movement overtook in-flight Stop')
        c.stop_block.set(); worker.join(1); b.stop_watchdog()
        self.assertFalse(worker.is_alive())
        names=[name for name,_ in c.calls]
        self.assertEqual(names[:2],['stop_move','continuous_move'])
        self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline)

    def test_absolute_movement_waits_for_inflight_safety_stop(self):
        r,c=self.runtime(); b=app.Bridge({}); b.cameras[r.camera_id]=r
        with r.stop_condition:r.stop_in_progress=True
        worker=threading.Thread(target=lambda:b.execute(r,'absolute',{'pan':.2,'tilt':.3}),daemon=True); worker.start()
        time.sleep(.05)
        self.assertFalse(any(name=='absolute_move' for name,_ in c.calls))
        with r.stop_condition:
            r.stop_in_progress=False; r.stop_condition.notify_all()
        worker.join(1)
        self.assertFalse(worker.is_alive()); self.assertEqual(c.calls[-1][0],'absolute_move')

    def test_pt_target_preserves_continuous_zoom_safety(self):
        r,c=self.runtime(); r.caps.update({'zoom_absolute':True,'zoom_relative':True,'zoom_continuous':True})
        b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'zoom':.5})
        self.assertFalse(r.moving_pt); self.assertTrue(r.moving_zoom)
        deadline=r.stop_deadline
        b.execute(r,'absolute',{'pan':.2,'tilt':.3})
        self.assertFalse(r.moving_pt); self.assertTrue(r.moving_zoom); self.assertTrue(r.moving)
        self.assertEqual(r.stop_deadline,deadline)
        b.watchdog_once(r,deadline+.01)
        self.assertEqual(c.calls[-1],('stop_move',{'pan_tilt':False,'zoom':True}))

    def test_target_with_zoom_retires_both_continuous_axes(self):
        r,c=self.runtime(); r.caps.update({'zoom_absolute':True,'zoom_relative':True,'zoom_continuous':True})
        b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'pan':.3,'zoom':.5})
        b.execute(r,'absolute',{'pan':.2,'tilt':.3,'zoom':.4})
        self.assertFalse(r.moving_pt); self.assertFalse(r.moving_zoom); self.assertFalse(r.moving)
        self.assertIsNone(r.stop_deadline)

    def test_successful_zero_zoom_velocity_retires_previous_zoom(self):
        r,c=self.runtime(); r.caps.update({'zoom_continuous':True})
        b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'zoom':.5})
        b.execute(r,'ptz',{'pan':.3,'zoom':0})
        self.assertTrue(r.moving_pt); self.assertFalse(r.moving_zoom)

    def test_failed_zero_zoom_velocity_keeps_previous_zoom_armed(self):
        r,c=self.runtime(); r.caps.update({'zoom_continuous':True})
        b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'zoom':.5}); c.move_failures=1
        with self.assertRaises(RuntimeError):b.execute(r,'ptz',{'pan':.3,'zoom':0})
        self.assertTrue(r.moving_pt); self.assertTrue(r.moving_zoom); self.assertTrue(r.moving)

    def test_partial_pt_patch_rereads_cache_after_inflight_stop(self):
        r,c=self.runtime(); safety=FakeClient(); r.safety_client=safety
        b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'tilt':.4})
        safety.stop_block=threading.Event()
        with r.stop_condition:
            r.stop_deadline_pt=time.monotonic()-1; b.sync_moving(r)
        stopper=threading.Thread(target=lambda:b.safety_stop_once(r),daemon=True); stopper.start()
        self.assertTrue(safety.stop_seen.wait(.4))
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.3}),daemon=True); worker.start()
        time.sleep(.03)
        self.assertTrue(worker.is_alive(),'partial patch did not wait for in-flight Stop')
        safety.stop_block.set(); stopper.join(1); worker.join(1)
        self.assertEqual(c.calls[-1],('continuous_move',{'pan':.3,'tilt':0.0,'zoom':None}))
        self.assertEqual((r.commanded_pan,r.commanded_tilt),(.3,0.0))

    def test_separate_pan_patch_preserves_previous_tilt(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'tilt':.4})
        b.execute(r,'ptz',{'pan':.3})
        self.assertEqual(c.calls[0],('continuous_move',{'pan':0.0,'tilt':.4,'zoom':None}))
        self.assertEqual(c.calls[1],('continuous_move',{'pan':.3,'tilt':.4,'zoom':None}))
        self.assertEqual((r.commanded_pan,r.commanded_tilt),(.3,.4))

    def test_explicit_single_pt_zero_preserves_other_component(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'pan':.3,'tilt':.4})
        b.execute(r,'ptz',{'pan':0})
        self.assertEqual(c.calls[-1],('continuous_move',{'pan':0.0,'tilt':.4,'zoom':None}))
        self.assertTrue(r.moving_pt); self.assertEqual((r.commanded_pan,r.commanded_tilt),(0.0,.4))

    def test_unsupported_zero_zoom_is_omitted_from_pt_request(self):
        r,c=self.runtime(); b=app.Bridge({})
        b.execute(r,'ptz',{'pan':.4,'tilt':0,'zoom':0})
        self.assertEqual(c.calls[-1],('continuous_move',{'pan':.4,'tilt':0.0,'zoom':None}))
        self.assertFalse(r.moving_zoom)

    def test_unsupported_zero_pt_is_omitted_from_zoom_request(self):
        r,c=self.runtime(); r.caps.update({'pan_tilt_continuous':False,'zoom_continuous':True})
        b=app.Bridge({})
        b.execute(r,'ptz',{'pan':0,'tilt':0,'zoom':.4})
        self.assertEqual(c.calls[-1],('continuous_move',{'pan':None,'tilt':None,'zoom':.4}))
        self.assertFalse(r.moving_pt); self.assertTrue(r.moving_zoom)

    def test_only_unsupported_zero_axes_is_noop_not_stop(self):
        r,c=self.runtime(); r.caps.update({'pan_tilt_continuous':False,'zoom_continuous':False})
        b=app.Bridge({})
        b.execute(r,'ptz',{'pan':0,'tilt':0,'zoom':0})
        self.assertEqual(c.calls,[])

    def test_ordinary_success_keeps_request_start_deadline(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
        c.move_block=threading.Event()
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.4}),daemon=True); worker.start()
        for _ in range(50):
            with r.stop_condition: deadline=r.stop_deadline_pt
            if deadline is not None:break
            time.sleep(.005)
        self.assertIsNotNone(deadline)
        time.sleep(.05); c.move_block.set(); worker.join(1)
        self.assertEqual(r.stop_deadline_pt,deadline)

    def test_axis_specific_watchdog_stop(self):
        for payload,expected in [
            ({'pan':.3},{'pan_tilt':True,'zoom':False}),
            ({'zoom':.4},{'pan_tilt':False,'zoom':True}),
            ({'pan':.3,'zoom':.4},{'pan_tilt':True,'zoom':True}),
        ]:
            with self.subTest(payload=payload):
                r,c=self.runtime(); r.caps.update({'zoom_continuous':True})
                b=app.Bridge({'ptz_safety_timeout_seconds':.05})
                b.execute(r,'ptz',payload)
                b.watchdog_once(r,r.stop_deadline+.01)
                self.assertEqual(c.calls[-1],('stop_move',expected))

    def test_watchdog_injected_clock_is_used_by_stop_claim(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'pan':.3})
        deadline=r.stop_deadline_pt
        self.assertTrue(b.watchdog_once(r,deadline+.01))
        self.assertEqual(c.calls[-1],('stop_move',{'pan_tilt':True,'zoom':False}))

    def test_pt_transition_has_short_deadline_while_zoom_keeps_normal_deadline(self):
        r,c=self.runtime(); r.caps.update({'zoom_continuous':True})
        b=app.Bridge({'ptz_safety_timeout_seconds':3,'ptz_transition_safety_seconds':.05})
        b.execute(r,'ptz',{'pan':.3,'zoom':.4})
        zoom_deadline=r.stop_deadline_zoom
        c.target_blocks['absolute_move']=threading.Event()
        worker=threading.Thread(target=lambda:b.execute(r,'absolute',{'pan':.2,'tilt':.3}),daemon=True); worker.start()
        self.assertTrue(c.target_seen.setdefault('absolute_move',threading.Event()).wait(.4))
        with r.stop_condition:
            self.assertLess(r.stop_deadline_pt,r.stop_deadline_zoom)
            pt_deadline=r.stop_deadline_pt
        b.watchdog_once(r,pt_deadline+.01)
        self.assertEqual(c.calls[-1],('stop_move',{'pan_tilt':True,'zoom':False}))
        self.assertTrue(r.moving_zoom); self.assertEqual(r.stop_deadline_zoom,zoom_deadline)
        c.target_blocks['absolute_move'].set(); worker.join(1)

    def test_late_success_rearms_deadline_after_completed_stop(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':.05}); b.cameras[r.camera_id]=r
        c.move_block=threading.Event(); b.start_watchdog()
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.4}),daemon=True); worker.start()
        try:
            self.assertTrue(c.stop_seen.wait(.5))
            for _ in range(50):
                if not r.moving:break
                time.sleep(.01)
            self.assertFalse(r.moving)
            c.move_block.set(); worker.join(1)
            self.assertTrue(r.moving_pt); self.assertIsNotNone(r.stop_deadline_pt); self.assertIsNotNone(r.stop_deadline)
        finally:
            c.move_block.set(); b.stop_watchdog()

    def test_target_moves_retire_pending_continuous_deadline(self):
        for action,payload,call_name in [
            ('absolute',{'pan':.2,'tilt':.3},'absolute_move'),
            ('relative',{'pan':.1,'tilt':0},'relative_move'),
            ('preset',{'token':'1'},'goto_preset'),
        ]:
            with self.subTest(action=action):
                r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
                b.execute(r,'ptz',{'pan':.4,'tilt':0})
                old_generation=r.movement_generation
                b.execute(r,action,payload)
                self.assertFalse(r.moving)
                self.assertIsNone(r.stop_deadline)
                self.assertGreater(r.movement_generation,old_generation)
                self.assertEqual(c.calls[-1][0],call_name)
                before=len(c.calls); b.watchdog_once(r,time.monotonic()+10)
                self.assertEqual(len(c.calls),before,'stale ContinuousMove watchdog fired after target move')

    def test_invalid_target_payload_does_not_retire_continuous_safety(self):
        r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'pan':.4,'tilt':0}); generation=r.movement_generation; deadline=r.stop_deadline
        with self.assertRaises((KeyError,ValueError)):b.execute(r,'absolute',{'tilt':.2})
        self.assertTrue(r.moving); self.assertEqual(r.movement_generation,generation); self.assertEqual(r.stop_deadline,deadline)
        self.assertEqual([name for name,_ in c.calls],['continuous_move'])

    def test_failed_target_request_rearms_immediate_safety_stop(self):
        for action,payload,call_name in [
            ('absolute',{'pan':.2,'tilt':.3},'absolute_move'),
            ('relative',{'pan':.1,'tilt':0},'relative_move'),
            ('preset',{'token':'1'},'goto_preset'),
        ]:
            with self.subTest(action=action):
                r,c=self.runtime(); b=app.Bridge({'ptz_safety_timeout_seconds':3})
                b.execute(r,'ptz',{'pan':.4,'tilt':0}); c.target_failures[call_name]=1
                with self.assertRaises(RuntimeError):b.execute(r,action,payload)
                self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline)
                before=len(c.calls); b.watchdog_once(r,time.monotonic()+.01)
                self.assertEqual(len(c.calls),before+1); self.assertEqual(c.calls[-1][0],'stop_move')

    def test_ptz_coalescing_stops_at_target_barrier(self):
        r,c=self.runtime(); b=app.Bridge({}); b.cameras[r.camera_id]=r
        first=(r.camera_id,'ptz',{'pan':.1})
        b.q.put((r.camera_id,'ptz',{'pan':.2}))
        b.q.put((r.camera_id,'absolute',{'pan':.5,'tilt':.5}))
        b.q.put((r.camera_id,'ptz',{'pan':.9}))
        latest=b.coalesce_ptz(first)
        self.assertEqual(latest[2]['pan'],.2)
        self.assertEqual(b.pending_command[1],'absolute')
        barrier=b.pending_command; b.pending_command=None
        self.assertEqual(barrier[2]['pan'],.5)
        self.assertEqual(b.q.get_nowait()[2]['pan'],.9)

    def test_ptz_coalescing_merges_partial_axes(self):
        r,c=self.runtime(); b=app.Bridge({})
        first=(r.camera_id,'ptz',{'zoom':0})
        b.q.put((r.camera_id,'ptz',{'pan':.3}))
        latest=b.coalesce_ptz(first)
        self.assertEqual(latest[2],{'zoom':0,'pan':.3})

    def test_ptz_coalescing_latest_value_wins_per_axis(self):
        r,c=self.runtime(); b=app.Bridge({})
        first=(r.camera_id,'ptz',{'pan':.1,'zoom':.2})
        b.q.put((r.camera_id,'ptz',{'pan':.4}))
        b.q.put((r.camera_id,'ptz',{'zoom':0}))
        latest=b.coalesce_ptz(first)
        self.assertEqual(latest[2],{'pan':.4,'zoom':0})

    def test_preset_resolves_active_zoom_before_goto(self):
        r,c=self.runtime(); r.caps.update({'zoom_continuous':True})
        b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'zoom':.5})
        b.execute(r,'preset',{'token':'1'})
        self.assertEqual([name for name,_ in c.calls],['continuous_move','stop_move','goto_preset'])
        self.assertEqual(c.calls[1],('stop_move',{'pan_tilt':False,'zoom':True}))
        self.assertFalse(r.moving_zoom)

    def test_failed_preset_zoom_prestop_does_not_issue_preset_and_rearms_zoom(self):
        r,c=self.runtime(); r.caps.update({'zoom_continuous':True})
        b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'zoom':.5}); c.stop_failures=1
        with self.assertRaises(RuntimeError):b.execute(r,'preset',{'token':'1'})
        self.assertNotIn('goto_preset',[name for name,_ in c.calls])
        self.assertTrue(r.moving_zoom); self.assertIsNotNone(r.stop_deadline_zoom)
        self.assertLessEqual(r.stop_deadline_zoom,time.monotonic()+.05)

    def test_ptz_stop_is_a_coalescing_barrier(self):
        r,c=self.runtime(); b=app.Bridge({}); first=(r.camera_id,'ptz',{'pan':.1})
        b.q.put((r.camera_id,'ptz',{'pan':.2})); b.q.put((r.camera_id,'ptz',{'stop':True})); b.q.put((r.camera_id,'ptz',{'pan':.8}))
        latest=b.coalesce_ptz(first)
        self.assertEqual(latest[2]['pan'],.2); self.assertTrue(b.pending_command[2]['stop'])
        self.assertEqual(b.q.get_nowait()[2]['pan'],.8)

    def test_failed_explicit_stop_preserves_safety_state(self):
        r,c=self.runtime(); b=app.Bridge({}); b.execute(r,'ptz',{'pan':.4,'tilt':0}); c.stop_failures=1
        with self.assertRaises(RuntimeError):b.execute(r,'ptz',{'stop':True})
        self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline)
        before=len(c.calls); b.watchdog_once(r,time.monotonic()+.01)
        self.assertEqual(c.calls[before][0],'stop_move')

    def test_clear_movement_invalidates_late_generation(self):
        r,c=self.runtime(); b=app.Bridge({}); generation=b.arm_movement(r)[0]
        b.clear_movement(r)
        self.assertGreater(r.movement_generation,generation); self.assertIsNone(r.stop_again_generation)
        with r.stop_condition:
            if r.movement_generation==generation:
                r.moving=True
        self.assertFalse(r.moving)

    def test_late_shutdown_stop_cannot_clear_new_generation(self):
        r,c=self.runtime(); safety=FakeClient(); r.safety_client=safety; b=app.Bridge({})
        b.arm_movement(r); safety.stop_block=threading.Event()
        worker=threading.Thread(target=lambda:b.shutdown_stop_one(r),daemon=True); worker.start()
        self.assertTrue(safety.stop_seen.wait(.4))
        # Model a newer generation arriving while the old shutdown request is pending.
        with r.stop_condition:
            r.movement_generation+=1; newer=r.movement_generation
            r.moving_pt=True; r.stop_deadline_pt=time.monotonic()+3; b.sync_moving(r)
        safety.stop_block.set(); worker.join(1)
        self.assertEqual(r.movement_generation,newer); self.assertTrue(r.moving); self.assertIsNotNone(r.stop_deadline)

    def test_shutdown_stops_are_dispatched_independently(self):
        r1,c1=self.runtime(); r1.camera_id='cam1'; r1.name='Camera 1'
        r2,c2=self.runtime(); r2.camera_id='cam2'; r2.name='Camera 2'
        b=app.Bridge({'shutdown_stop_wait_seconds':.1}); b.cameras={'cam1':r1,'cam2':r2}
        c1.stop_block=threading.Event(); b.arm_movement(r1); b.arm_movement(r2)
        started=time.monotonic()
        try:
            b.shutdown_stops()
            elapsed=time.monotonic()-started
            self.assertTrue(c1.stop_seen.is_set())
            self.assertTrue(c2.stop_seen.is_set(),'camera 2 shutdown Stop was starved by camera 1')
            self.assertLess(elapsed,.5,'shutdown waited on a blocked camera request')
            self.assertFalse(r2.moving)
        finally:
            c1.stop_block.set()


    def test_nonfinite_velocity_is_rejected_without_camera_io(self):
        r,c=self.runtime(); b=app.Bridge({})
        for payload in ({'pan':float('nan')},{'tilt':float('inf')},{'zoom':float('-inf')}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):b.execute(r,'ptz',payload)
        self.assertEqual(c.calls,[]); self.assertFalse(r.moving)

    def test_pt_success_reconciles_during_unrelated_zoom_stop(self):
        r,c=self.runtime(); r.caps['zoom_continuous']=True
        safety=FakeClient(); r.safety_client=safety
        b=app.Bridge({'ptz_safety_timeout_seconds':3})
        b.execute(r,'ptz',{'pan':.1,'tilt':.2,'zoom':.4})
        c.move_block=threading.Event()
        worker=threading.Thread(target=lambda:b.execute(r,'ptz',{'pan':.3}),daemon=True); worker.start()
        for _ in range(50):
            if len([x for x in c.calls if x[0]=='continuous_move'])>=2:break
            time.sleep(.01)
        with r.stop_condition:
            r.stop_deadline_pt=time.monotonic()+10
            r.stop_deadline_zoom=time.monotonic()-1
            b.sync_moving(r)
        safety.stop_block=threading.Event()
        stopper=threading.Thread(target=lambda:b.watchdog_once(r,time.monotonic()),daemon=True); stopper.start()
        self.assertTrue(safety.stop_seen.wait(.5))
        c.move_block.set(); worker.join(1)
        self.assertEqual((r.commanded_pan,r.commanded_tilt),(.3,.2))
        self.assertTrue(r.moving_pt)
        safety.stop_block.set(); stopper.join(1)
        self.assertFalse(r.moving_zoom)
        self.assertTrue(r.moving_pt)

    def test_target_replays_after_overtaking_watchdog_stop(self):
        r,c=self.runtime(); safety=FakeClient(); r.safety_client=safety
        b=app.Bridge({'ptz_safety_timeout_seconds':3,'ptz_transition_safety_seconds':.02})
        b.execute(r,'ptz',{'pan':.4,'tilt':0})
        c.target_blocks['absolute_move']=threading.Event()
        worker=threading.Thread(target=lambda:b.execute(r,'absolute',{'pan':.2,'tilt':.3}),daemon=True); worker.start()
        self.assertTrue(c.target_seen.setdefault('absolute_move',threading.Event()).wait(.5))
        time.sleep(.03)
        self.assertTrue(b.watchdog_once(r,time.monotonic()))
        c.target_blocks['absolute_move'].set(); worker.join(1)
        calls=[name for name,_ in c.calls]
        self.assertEqual(calls.count('absolute_move'),2)
        self.assertFalse(r.moving)


    def test_absolute_and_relative(self):
        r,c=self.runtime(); b=app.Bridge({}); b.execute(r,'absolute',{'pan':.2,'tilt':.58,'speed':.2}); b.execute(r,'relative',{'pan':.05,'tilt':0,'speed':.2}); self.assertEqual([x[0] for x in c.calls],['absolute_move','relative_move'])

if __name__=='__main__':unittest.main()
