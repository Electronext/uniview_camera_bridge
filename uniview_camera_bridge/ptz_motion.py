from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class _PTZState:
    source_id: int
    primary: Any
    safety: Any
    generation: int = 0
    moving: bool = False
    deadline: float | None = None
    pending: list[tuple[int, str, Any]] = field(default_factory=list)
    worker: threading.Thread | None = None
    stop_required: bool = False
    stop_retry_due: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    stop_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    stop_done: threading.Event = field(default_factory=threading.Event, repr=False)


class UniviewPTZMotionManager:
    """Continuous-PTZ transport with an independent Stop path.

    ContinuousMove requests are serialized per camera on a daemon worker so a
    slow camera cannot block the bridge's MQTT/main loop. Release and watchdog
    Stops use a forked ONVIF session and therefore can overtake a blocked move.
    If a blocked/ambiguous move completes after a Stop, the worker sends one
    follow-up Stop before it is allowed to send any newer queued movement.
    """

    def __init__(self, safety_timeout: float = 3.0, stop_retry: float = 0.5, watchdog_interval: float = 0.05):
        self.safety_timeout = max(0.05, float(safety_timeout))
        self.stop_retry = max(0.1, float(stop_retry))
        self.watchdog_interval = max(0.02, min(0.25, float(watchdog_interval)))
        self.states: dict[int, _PTZState] = {}
        self._shutdown = threading.Event()
        self._watchdog: threading.Thread | None = None

    def register(self, source_id: int, primary: Any, safety: Any) -> None:
        state = _PTZState(source_id, primary, safety)
        state.stop_done.set()
        self.states[source_id] = state

    def start(self) -> None:
        if self._watchdog and self._watchdog.is_alive():
            return
        self._shutdown.clear()
        self._watchdog = threading.Thread(target=self._watchdog_loop, name="uniview-ptz-safety", daemon=True)
        self._watchdog.start()

    @staticmethod
    def _velocity(value: Any) -> float:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("PTZ velocities must be finite")
        return max(-1.0, min(1.0, value))

    def _ensure_worker_locked(self, state: _PTZState) -> None:
        if state.worker is None or not state.worker.is_alive():
            state.worker = threading.Thread(target=self._worker_loop, args=(state,), name=f"uniview-ptz-D{state.source_id}", daemon=True)
            state.worker.start()

    def submit_move(self, source_id: int, pan: Any, tilt: Any, zoom: Any) -> None:
        state = self.states[source_id]
        pan, tilt, zoom = self._velocity(pan), self._velocity(tilt), self._velocity(zoom)
        with state.lock:
            state.generation += 1
            generation = state.generation
            state.moving = True
            state.deadline = time.monotonic() + self.safety_timeout
            state.stop_required = False
            state.stop_retry_due = None
            # Velocity samples are replaceable until the worker claims one.
            # Replace only a still-pending velocity tail. Targets are barriers.
            if state.pending and state.pending[-1][1] == "move":
                state.pending[-1] = (generation, "move", (pan, tilt, zoom))
            else:
                state.pending.append((generation, "move", (pan, tilt, zoom)))
            self._ensure_worker_locked(state)

    def submit_target(self, source_id: int, send: Callable[[], Any]) -> threading.Event:
        """Queue an absolute/preset target behind already-submitted PTZ work.

        The target is a hard per-camera ordering barrier. It invalidates the
        continuous-movement watchdog state immediately, then executes on the
        same primary-session worker after any already-claimed ContinuousMove
        and its required follow-up Stop have completed.
        """
        state = self.states[source_id]
        done = threading.Event()
        with state.lock:
            state.generation += 1
            generation = state.generation
            # The target supersedes continuous motion, but keep the old
            # watchdog armed until the worker has issued the target's
            # pre-Stop. This avoids an unbounded interval if a claimed
            # ContinuousMove is still blocked.
            state.pending.append((generation, "target", (send, done)))
            self._ensure_worker_locked(state)
        return done

    def _worker_loop(self, state: _PTZState) -> None:
        while not self._shutdown.is_set():
            with state.lock:
                if not state.pending:
                    state.worker = None
                    return
                generation, kind, payload = state.pending.pop(0)
                # Older work is still meaningful when a target barrier follows
                # it; explicit Stop clears the queue instead.
                if kind == "move" and not state.moving and not state.pending:
                    continue
            # Never let newly queued work overtake an already-started safety
            # Stop. Revalidate after it completes.
            state.stop_done.wait()
            with state.lock:
                if kind == "move" and not state.moving and not state.pending:
                    continue
            if kind == "target":
                send, done = payload
                # Establish a hard Stop barrier before an absolute/preset
                # target. Hold stop_lock across Stop -> target so an already
                # claimed watchdog Stop cannot land after the target.
                barrier_ok = False
                with state.stop_lock:
                    state.stop_done.clear()
                    try:
                        while not self._shutdown.is_set():
                            try:
                                state.safety.stop_move(pan_tilt=True, zoom=True)
                                barrier_ok = True
                                break
                            except Exception:
                                logging.exception("D%d PTZ target pre-Stop failed; retrying barrier", state.source_id)
                                with state.lock:
                                    state.stop_required = True
                                    state.stop_retry_due = None
                                self._shutdown.wait(self.stop_retry)
                        if barrier_ok:
                            with state.lock:
                                # Clear only state owned by this target
                                # generation. A newer move may already have
                                # armed its own deadline while we were stopping.
                                if state.generation == generation:
                                    state.moving = False
                                    state.deadline = None
                                    state.stop_required = False
                                    state.stop_retry_due = None
                            try:
                                send()
                            except Exception:
                                logging.exception("D%d PTZ target request failed", state.source_id)
                    finally:
                        state.stop_done.set()
                done.set()
                continue
            pan, tilt, zoom = payload
            try:
                state.primary.continuous_move(pan=pan, tilt=tilt, zoom=zoom)
            except Exception:
                logging.exception("D%d PTZ ContinuousMove failed; safety Stop remains armed", state.source_id)
            finally:
                with state.lock:
                    overtaken = generation != state.generation
                if overtaken:
                    # A Stop or target overtook this request. The camera may
                    # nevertheless have accepted the late request, so Stop once
                    # more before the per-camera worker can send later work.
                    self._send_followup_stop(state)

    def _send_followup_stop(self, state: _PTZState) -> None:
        with state.stop_lock:
            state.stop_done.clear()
            try:
                state.safety.stop_move(pan_tilt=True, zoom=True)
            except Exception:
                logging.exception("D%d PTZ follow-up Stop failed; scheduling retry", state.source_id)
                with state.lock:
                    state.stop_required = True
                    state.stop_retry_due = time.monotonic() + self.stop_retry
            else:
                with state.lock:
                    # Do not clear a newer movement generation. The follow-up
                    # Stop is ordered before that movement because the primary
                    # worker is still inside this method.
                    state.stop_required = False
                    state.stop_retry_due = None
            finally:
                state.stop_done.set()

    def stop(self, source_id: int) -> bool:
        state = self.states[source_id]
        with state.lock:
            state.generation += 1
            generation = state.generation
            state.pending.clear()
            state.moving = False
            state.deadline = None
            state.stop_required = True
            state.stop_retry_due = time.monotonic()
        return self._attempt_stop(state, generation)

    def _attempt_stop(self, state: _PTZState, expected_generation: int) -> bool:
        with state.stop_lock:
            state.stop_done.clear()
            try:
                state.safety.stop_move(pan_tilt=True, zoom=True)
            except Exception:
                with state.lock:
                    if state.generation == expected_generation and state.stop_required:
                        state.stop_retry_due = time.monotonic() + self.stop_retry
                logging.exception("D%d PTZ Stop failed; retrying in %.1f s", state.source_id, self.stop_retry)
                return False
            finally:
                state.stop_done.set()
        with state.lock:
            if state.generation == expected_generation:
                state.stop_required = False
                state.stop_retry_due = None
        return True

    def watchdog_once(self, now_mono: float | None = None) -> None:
        now_mono = time.monotonic() if now_mono is None else now_mono
        claims: list[tuple[_PTZState, int]] = []
        for state in self.states.values():
            with state.lock:
                due_move = state.moving and state.deadline is not None and now_mono >= state.deadline
                due_retry = state.stop_required and state.stop_retry_due is not None and now_mono >= state.stop_retry_due
                if not due_move and not due_retry:
                    continue
                if due_move:
                    state.generation += 1
                    state.pending = [item for item in state.pending if item[1] == "target"]
                    state.moving = False
                    state.deadline = None
                    state.stop_required = True
                generation = state.generation
                # Claim the retry so another watchdog tick cannot duplicate it.
                state.stop_retry_due = None
                claims.append((state, generation))
        for state, generation in claims:
            threading.Thread(
                target=self._attempt_stop,
                args=(state, generation),
                name=f"uniview-ptz-watchdog-D{state.source_id}",
                daemon=True,
            ).start()

    def _watchdog_loop(self) -> None:
        while not self._shutdown.wait(self.watchdog_interval):
            self.watchdog_once()

    def is_moving(self, source_id: int) -> bool:
        state = self.states.get(source_id)
        if state is None:
            return False
        with state.lock:
            return state.moving or state.stop_required

    def shutdown(self, wait: float = 0.5) -> None:
        # Stop active/ambiguous cameras before terminating the watchdog. Each
        # Stop is dispatched independently so one dead camera cannot block the
        # others.
        workers: list[threading.Thread] = []
        for source_id, state in self.states.items():
            with state.lock:
                active = state.moving or state.stop_required or (state.worker is not None and state.worker.is_alive())
            if active:
                thread = threading.Thread(target=self.stop, args=(source_id,), name=f"uniview-ptz-shutdown-D{source_id}", daemon=True)
                workers.append(thread)
                thread.start()
        deadline = time.monotonic() + max(0.1, float(wait))
        for thread in workers:
            thread.join(max(0.0, deadline - time.monotonic()))
        self._shutdown.set()
        if self._watchdog and self._watchdog is not threading.current_thread():
            self._watchdog.join(timeout=0.25)
