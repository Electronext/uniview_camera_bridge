import importlib.util
import queue
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'uniview_camera_bridge'
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('ptz_motion',ROOT/'ptz_motion.py')
motion=importlib.util.module_from_spec(spec); sys.modules['ptz_motion']=motion; spec.loader.exec_module(motion)


class FakePrimary:
    def __init__(self):
        self.calls=[]; self.block=None; self.seen=threading.Event()
    def continuous_move(self,**kw):
        self.calls.append(('move',kw)); self.seen.set()
        if self.block:self.block.wait(2)


class FakeSafety:
    def __init__(self):
        self.calls=[]; self.failures=0; self.block=None; self.seen=threading.Event()
    def stop_move(self,**kw):
        self.calls.append(('stop',kw)); self.seen.set()
        if self.block:self.block.wait(2)
        if self.failures:
            self.failures-=1
            raise RuntimeError('stop failed')


class MotionTests(unittest.TestCase):
    def manager(self,timeout=.05):
        primary=FakePrimary(); safety=FakeSafety()
        manager=motion.UniviewPTZMotionManager(safety_timeout=timeout,stop_retry=.02,watchdog_interval=.01)
        manager.register(2,primary,safety)
        return manager,primary,safety

    def test_explicit_stop_overtakes_blocked_continuous_move(self):
        manager,primary,safety=self.manager()
        primary.block=threading.Event(); manager.start()
        try:
            manager.submit_move(2,.4,0,0)
            self.assertTrue(primary.seen.wait(.3))
            manager.stop(2)
            self.assertTrue(safety.seen.wait(.2))
            self.assertTrue(manager.states[2].worker.is_alive())
            primary.block.set()
            for _ in range(50):
                if len(safety.calls)>=2:break
                time.sleep(.01)
            self.assertGreaterEqual(len(safety.calls),2,'late ContinuousMove did not receive a follow-up Stop')
        finally:
            primary.block.set(); manager.shutdown()

    def test_new_move_waits_until_late_request_followup_stop(self):
        manager,primary,safety=self.manager(timeout=1)
        primary.block=threading.Event(); manager.start()
        try:
            manager.submit_move(2,.4,0,0); self.assertTrue(primary.seen.wait(.3))
            manager.stop(2)
            manager.submit_move(2,-.3,0,0)
            primary.block.set()
            for _ in range(50):
                if len(primary.calls)>=2:break
                time.sleep(.01)
            self.assertEqual(len(primary.calls),2)
            self.assertGreaterEqual(len(safety.calls),2)
            self.assertEqual(primary.calls[1][1]['pan'],-.3)
        finally:
            primary.block.set(); manager.shutdown()

    def test_new_move_cannot_overtake_inflight_stop(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        try:
            manager.submit_move(2,.4,0,0); self.assertTrue(primary.seen.wait(.3))
            safety.block=threading.Event()
            stopper=threading.Thread(target=lambda:manager.stop(2),daemon=True); stopper.start()
            self.assertTrue(safety.seen.wait(.3))
            primary.seen.clear(); manager.submit_move(2,-.4,0,0)
            self.assertFalse(primary.seen.wait(.05),'new move overtook in-flight Stop')
            safety.block.set(); stopper.join(1)
            self.assertTrue(primary.seen.wait(.3),'new move did not resume after Stop completed')
        finally:
            if safety.block:safety.block.set()
            manager.shutdown()

    def test_watchdog_stop_retries_after_failure(self):
        manager,primary,safety=self.manager(timeout=.02)
        safety.failures=1; manager.start()
        try:
            manager.submit_move(2,.4,0,0)
            for _ in range(100):
                if len(safety.calls)>=2:break
                time.sleep(.01)
            self.assertGreaterEqual(len(safety.calls),2)
        finally:
            manager.shutdown()

    def test_nonfinite_velocity_is_rejected_before_state_change(self):
        manager,primary,safety=self.manager()
        with self.assertRaises(ValueError):manager.submit_move(2,float('nan'),0,0)
        self.assertFalse(manager.states[2].moving); self.assertEqual(primary.calls,[])


class CoalescingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # app imports optional runtime dependencies available in the add-on;
        # only load it when they are installed in the test environment.
        try:
            spec=importlib.util.spec_from_file_location('uniview_app',ROOT/'app.py')
            cls.app=importlib.util.module_from_spec(spec); sys.modules['uniview_app']=cls.app; spec.loader.exec_module(cls.app)
        except ModuleNotFoundError:
            cls.app=None

    def setUp(self):
        if self.app is None:self.skipTest('Uniview app runtime dependencies unavailable')

    def test_stop_is_ordering_barrier(self):
        q=queue.Queue()
        first={'action':'camera_ptz','source_id':2,'pan':.1}
        q.put({'action':'camera_ptz','source_id':2,'pan':.2})
        q.put({'action':'camera_ptz','source_id':2,'stop':True})
        q.put({'action':'camera_ptz','source_id':2,'pan':.9})
        latest,barrier,count=self.app.coalesce_camera_ptz(first,q)
        self.assertEqual(latest['pan'],.2); self.assertEqual(count,1)
        self.assertTrue(barrier['stop'])
        self.assertEqual(q.get_nowait()['pan'],.9)

    def test_unrelated_command_is_not_reordered(self):
        q=queue.Queue()
        first={'action':'camera_ptz','source_id':2,'pan':.1}
        q.put({'action':'camera_snapshot','source_id':4})
        q.put({'action':'camera_ptz','source_id':2,'pan':.9})
        latest,barrier,count=self.app.coalesce_camera_ptz(first,q)
        self.assertEqual(latest['pan'],.1); self.assertEqual(count,0)
        self.assertEqual(barrier['action'],'camera_snapshot')
        self.assertEqual(q.get_nowait()['pan'],.9)


if __name__=='__main__':unittest.main()
