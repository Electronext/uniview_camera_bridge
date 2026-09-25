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

    def test_watchdog_stops_are_isolated_per_camera(self):
        manager,p1,s1=self.manager(timeout=.02)
        p2=FakePrimary(); s2=FakeSafety(); manager.register(3,p2,s2)
        s1.block=threading.Event(); manager.start()
        try:
            manager.submit_move(2,.4,0,0); manager.submit_move(3,.4,0,0)
            self.assertTrue(s1.seen.wait(.4))
            self.assertTrue(s2.seen.wait(.4),'camera 3 Stop was starved by blocked camera 2 Stop')
        finally:
            s1.block.set(); manager.shutdown()

    def test_target_is_ordered_after_claimed_velocity_and_followup_stop(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        primary.block=threading.Event()
        target_seen=threading.Event()
        try:
            manager.submit_move(2,.4,0,0); self.assertTrue(primary.seen.wait(.3))
            manager.submit_target(2,lambda:target_seen.set())
            self.assertFalse(target_seen.wait(.05),'target overtook claimed ContinuousMove')
            primary.block.set()
            self.assertTrue(target_seen.wait(.4))
            self.assertGreaterEqual(len(safety.calls),1,'continuous velocity was not stopped before target')
        finally:
            primary.block.set(); manager.shutdown()

    def test_target_after_completed_velocity_still_has_stop_barrier(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        target_seen=threading.Event()
        try:
            manager.submit_move(2,.4,0,0); self.assertTrue(primary.seen.wait(.3))
            for _ in range(50):
                if manager.states[2].worker is None:break
                time.sleep(.01)
            manager.submit_target(2,lambda:target_seen.set())
            self.assertTrue(target_seen.wait(.4))
            self.assertGreaterEqual(len(safety.calls),1)
        finally:
            manager.shutdown()

    def test_watchdog_expiry_preserves_queued_target(self):
        manager,primary,safety=self.manager(timeout=.02); manager.start()
        primary.block=threading.Event(); target_seen=threading.Event()
        try:
            manager.submit_move(2,.4,0,0); self.assertTrue(primary.seen.wait(.3))
            manager.submit_target(2,lambda:target_seen.set())
            time.sleep(.04); manager.watchdog_once(time.monotonic())
            primary.block.set()
            self.assertTrue(target_seen.wait(.5),'watchdog discarded queued target')
        finally:
            primary.block.set(); manager.shutdown()

    def test_failed_target_prestop_retries_before_target(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        safety.failures=1; target_seen=threading.Event()
        try:
            manager.submit_target(2,lambda:target_seen.set())
            self.assertTrue(target_seen.wait(.5))
            self.assertGreaterEqual(len(safety.calls),2)
        finally:
            manager.shutdown()

    def test_move_queued_during_target_barrier_is_preserved(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        safety.block=threading.Event(); target_seen=threading.Event()
        try:
            manager.submit_target(2,lambda:target_seen.set())
            self.assertTrue(safety.seen.wait(.3))
            primary.seen.clear(); manager.submit_move(2,.6,0,0)
            safety.block.set()
            self.assertTrue(target_seen.wait(.3))
            self.assertTrue(primary.seen.wait(.4),'move queued behind target was lost')
            self.assertEqual(primary.calls[-1][1]['pan'],.6)
        finally:
            safety.block.set(); manager.shutdown()

    def test_explicit_stop_cancels_claimed_target_before_send(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        safety.block=threading.Event(); target_seen=threading.Event()
        try:
            manager.submit_target(2,lambda:target_seen.set())
            self.assertTrue(safety.seen.wait(.3),'target pre-Stop did not start')
            stopper=threading.Thread(target=lambda:manager.stop(2),daemon=True); stopper.start()
            for _ in range(50):
                if manager.states[2].generation>=2:break
                time.sleep(.01)
            safety.block.set(); stopper.join(1)
            self.assertFalse(target_seen.is_set(),'explicit Stop did not cancel claimed target')
        finally:
            safety.block.set(); manager.shutdown()

    def test_watchdog_claim_blocks_target_before_stop_thread_runs(self):
        manager,primary,safety=self.manager(timeout=.02); manager.start()
        safety.block=threading.Event(); target_seen=threading.Event()
        try:
            manager.submit_move(2,.4,0,0)
            with manager.states[2].lock:
                due=manager.states[2].deadline
            manager.watchdog_once(due + .001)
            self.assertFalse(manager.states[2].stop_done.is_set(),'watchdog claim did not synchronously close ordering gate')
            manager.submit_target(2,lambda:target_seen.set())
            self.assertFalse(target_seen.wait(.05),'target overtook claimed watchdog Stop')
            safety.block.set()
            self.assertTrue(target_seen.wait(.5))
        finally:
            safety.block.set(); manager.shutdown()

    def test_later_move_does_not_cancel_claimed_target(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        safety.block=threading.Event(); target_seen=threading.Event()
        try:
            manager.submit_target(2,lambda:target_seen.set())
            self.assertTrue(safety.seen.wait(.3))
            primary.seen.clear(); manager.submit_move(2,.7,0,0)
            safety.block.set()
            self.assertTrue(target_seen.wait(.4),'later move cancelled ordered target')
            self.assertTrue(primary.seen.wait(.4),'later move was not sent after target')
        finally:
            safety.block.set(); manager.shutdown()

    def test_failed_followup_stop_blocks_later_move_until_retry_succeeds(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        primary.block=threading.Event()
        try:
            manager.submit_move(2,.4,0,0); self.assertTrue(primary.seen.wait(.3))
            manager.stop(2)
            safety.failures=1
            primary.seen.clear(); manager.submit_move(2,-.4,0,0)
            primary.block.set()
            time.sleep(.05)
            self.assertFalse(primary.seen.is_set(),'later move overtook failed follow-up Stop')
            for _ in range(60):
                if primary.seen.is_set():break
                time.sleep(.01)
            self.assertTrue(primary.seen.is_set(),'later move did not resume after follow-up Stop retry')
        finally:
            primary.block.set(); manager.shutdown()

    def test_watchdog_drops_newer_velocity_but_preserves_target_validity(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        primary.block=threading.Event(); target_seen=threading.Event()
        try:
            manager.submit_move(2,.2,0,0); self.assertTrue(primary.seen.wait(.3))
            manager.submit_target(2,lambda:target_seen.set())
            manager.submit_move(2,.8,0,0)
            with manager.states[2].lock:
                due=manager.states[2].deadline
            manager.watchdog_once(due + .001)
            primary.block.set()
            self.assertTrue(target_seen.wait(.5),'watchdog invalidated preserved target')
            self.assertEqual(len(primary.calls),1,'expired newer velocity should have been discarded')
        finally:
            primary.block.set(); manager.shutdown()

    def test_cancelled_target_breaks_prestop_retry_loop(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        safety.failures=100; target_seen=threading.Event()
        manager.submit_target(2,lambda:target_seen.set())
        time.sleep(.03)
        stopper=threading.Thread(target=lambda:manager.stop(2),daemon=True); stopper.start()
        stopper.join(.6)
        self.assertFalse(stopper.is_alive(),'explicit Stop remained blocked behind cancelled target retries')
        self.assertFalse(target_seen.is_set())

    def test_watchdog_claim_during_target_barrier_is_consumed_before_send(self):
        manager,primary,safety=self.manager(timeout=1); manager.start()
        safety.block=threading.Event(); target_seen=threading.Event()
        try:
            manager.submit_target(2,lambda:target_seen.set())
            self.assertTrue(safety.seen.wait(.3))
            manager.submit_move(2,.8,0,0)
            with manager.states[2].lock:
                due=manager.states[2].deadline
            manager.watchdog_once(due + .001)
            safety.block.set()
            self.assertTrue(target_seen.wait(.5))
            time.sleep(.05)
            # One pre-Stop satisfies the watchdog claim; no stale claimed Stop
            # may land after the target.
            self.assertEqual(len(safety.calls),1)
        finally:
            safety.block.set(); manager.shutdown()

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

    def test_legacy_zero_vector_release_is_ordering_barrier(self):
        q=queue.Queue()
        first={'action':'camera_ptz','source_id':2,'pan':.1,'tilt':0,'zoom':0}
        q.put({'action':'camera_ptz','source_id':2,'pan':0,'tilt':0,'zoom':0})
        q.put({'action':'camera_ptz','source_id':2,'pan':.9,'tilt':0,'zoom':0})
        latest,barrier,count=self.app.coalesce_camera_ptz(first,q)
        self.assertEqual(latest['pan'],.1); self.assertEqual(count,0)
        self.assertFalse(self.app.camera_ptz_is_velocity(barrier))
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
