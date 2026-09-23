from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _PTZState:
    source_id: int
    primary: Any
    safety: Any
    generation: int = 0
    moving: bool = False
    deadline: float | None = None
    pending: tuple[int, float, float, float] | None = None
    worker: threading.Thread | None = None
    stop_required: bool = False
    stop_retry_due: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


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
        self.states[source_id] = _PTZState(source_id, primary, safety)

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
            state.pending = (generation, pan, tilt, zoom)
            if state.worker is None or not state.worker.is_alive():
                state.worker = threading.Thread(target=self._worker_loop, args=(state,), name=f"uniview-ptz-D{source_id}", daemon=True)
                state.worker.start()

    def _worker_loop(self, state: _PTZState) -> None:
        while not self._shutdown.is_set():
            with state.lock:
                item = state.pending
                state.pending = None
                if item is None:
                    state.worker = None
                    return
                generation, pan, tilt, zoom = item
                if generation != state.generation or not state.moving:
                    continue
            try:
                state.primary.continuous_move(pan=pan, tilt=tilt, zoom=zoom)
            except Exception:
                logging.exception("D%d PTZ ContinuousMove failed; safety Stop remains armed", state.source_id)
            finally:
                with state.lock:
                    overtaken = generation != state.generation
                if overtaken:
                    # A release/watchdog Stop overtook this request. The camera
                    # may nevertheless have accepted the late request, so Stop
                    # once more before any newer primary-session move is sent.
                    self._send_followup_stop(state)

    def _send_followup_stop(self, state: _PTZState) -> None:
        try:
            state.safety.stop_move(pan_tilt=True, zoom=True)
        except Exception:
            logging.exception("D%d PTZ follow-up Stop failed; scheduling retry", state.source_id)
            with state.lock:
                state.stop_required = True
                state.stop_retry_due = time.monotonic() + self.stop_retry
        else:
            with state.lock:
                # Do not clear a newer movement generation. The follow-up Stop
                # is ordered before that movement because the primary worker is
                # still inside this method.
                state.stop_required = False
                state.stop_retry_due = None

    def stop(self, source_id: int) -> bool:
        state = self.states[source_id]
        with state.lock:
            state.generation += 1
            generation = state.generation
            state.pending = None
            state.moving = False
            state.deadline = None
            state.stop_required = True
            state.stop_retry_due = time.monotonic()
        return self._attempt_stop(state, generation)

    def _attempt_stop(self, state: _PTZState, expected_generation: int) -> bool:
        try:
            state.safety.stop_move(pan_tilt=True, zoom=True)
        except Exception:
            with state.lock:
                if state.generation == expected_generation and state.stop_required:
                    state.stop_retry_due = time.monotonic() + self.stop_retry
            logging.exception("D%d PTZ Stop failed; retrying in %.1f s", state.source_id, self.stop_retry)
            return False
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
                    state.pending = None
                    state.moving = False
                    state.deadline = None
                    state.stop_required = True
                generation = state.generation
                # Claim the retry so another watchdog tick cannot duplicate it.
                state.stop_retry_due = None
                claims.append((state, generation))
        for state, generation in claims:
            self._attempt_stop(state, generation)

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
