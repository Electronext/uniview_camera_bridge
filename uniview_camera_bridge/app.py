from __future__ import annotations

import csv
import json
import logging
import math
import os
import queue
import signal
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ha_mqtt import HomeAssistantClient, MQTTDiscovery
from alarm_service import AlarmServiceBridge, UniviewEvent
from uniview import UniviewCamera

OPTIONS_PATH = Path("/data/options.json")
PERSIST_DIR = Path("/config")
STATE_PATH = PERSIST_DIR / "state.json"
STATUS_PATH = PERSIST_DIR / "status.json"
HISTORY_PATH = PERSIST_DIR / "position_history.csv"
DIAGNOSTIC_PATH = PERSIST_DIR / "latest_match.jpg"
TEMPLATE_DIR = Path("/app/templates")

stop_requested = False


def request_stop(_signum: int, _frame: Any) -> None:
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)




class CameraEventState:
    FLAG_FIELDS = (
        "person_detected",
        "vehicle_detected",
        "non_motor_vehicle_detected",
        "face_detected",
        "smart_motion",
        "motion",
        "line_crossing",
        "intrusion",
        "human_shape_detect",
    )

    def __init__(self, mqtt: MQTTDiscovery, hold_seconds: int = 15):
        self.mqtt = mqtt
        self.hold_seconds = max(1, int(hold_seconds))
        self._states: dict[int, dict[str, Any]] = {}
        self._deadlines: dict[tuple[int, str], float] = {}
        self._lock = threading.Lock()

    def _state(self, source_id: int) -> dict[str, Any]:
        state = self._states.setdefault(source_id, {field: False for field in self.FLAG_FIELDS})
        state.setdefault("last_event_type", None)
        state.setdefault("last_raw_event_type", None)
        state.setdefault("last_object_class", None)
        state.setdefault("last_object_id", None)
        state.setdefault("last_event_time", None)
        return state

    def handle(self, event: UniviewEvent) -> None:
        if event.source_id <= 0:
            logging.warning("Ignoring Uniview event without a valid source ID: %s", event.raw_type)
            return
        with self._lock:
            state = self._state(event.source_id)
            state["last_event_type"] = event.event_type
            state["last_raw_event_type"] = event.raw_type
            if event.object_class is not None:
                state["last_object_class"] = event.object_class
            if event.object_id is not None:
                state["last_object_id"] = event.object_id
            state["last_event_time"] = event.timestamp

            now_mono = time.monotonic()
            if event.event_type in state:
                if event.active is False:
                    state[event.event_type] = False
                    self._deadlines.pop((event.source_id, event.event_type), None)
                elif event.active:
                    state[event.event_type] = True
                    self._deadlines[(event.source_id, event.event_type)] = now_mono + self.hold_seconds

            class_flag = {
                "person": "person_detected",
                "vehicle": "vehicle_detected",
                "non_motor_vehicle": "non_motor_vehicle_detected",
                "face": "face_detected",
            }.get(event.object_class or "")
            if class_flag and event.active is not False:
                state[class_flag] = True
                self._deadlines[(event.source_id, class_flag)] = now_mono + self.hold_seconds

            for cls, field in (("person", "person_detected"), ("vehicle", "vehicle_detected"), ("non_motor_vehicle", "non_motor_vehicle_detected"), ("face", "face_detected")):
                if event.counts.get(cls, 0) > 0:
                    state[field] = True
                    self._deadlines[(event.source_id, field)] = now_mono + self.hold_seconds

            published = dict(state)
            attrs = None
            if event.full_jpeg or event.crop_jpeg or event.bbox:
                attrs = event.metadata()
                if event.person_attributes is not None:
                    attrs["person_attributes"] = event.person_attributes

        self.mqtt.publish_camera_event_state(event.source_id, published, attrs)
        if event.annotated_jpeg:
            self.mqtt.publish_camera_image(event.source_id, event.annotated_jpeg, crop=False)
        elif event.full_jpeg:
            self.mqtt.publish_camera_image(event.source_id, event.full_jpeg, crop=False)
        if event.crop_jpeg:
            self.mqtt.publish_camera_image(event.source_id, event.crop_jpeg, crop=True)
        stream_event = event.metadata()
        stream_event["has_event_image"] = bool(event.annotated_jpeg or event.full_jpeg)
        stream_event["has_object_crop"] = bool(event.crop_jpeg)
        self.mqtt.publish_camera_event_message(stream_event)

    def expire(self) -> None:
        updates: list[tuple[int, dict[str, Any]]] = []
        with self._lock:
            now_mono = time.monotonic()
            affected: set[int] = set()
            for key, deadline in list(self._deadlines.items()):
                if deadline > now_mono:
                    continue
                source_id, field = key
                self._deadlines.pop(key, None)
                state = self._state(source_id)
                if state.get(field):
                    state[field] = False
                    affected.add(source_id)
            for source_id in affected:
                updates.append((source_id, dict(self._state(source_id))))
        for source_id, state in updates:
            self.mqtt.publish_camera_event_state(source_id, state)


@dataclass(frozen=True)
class MatchResult:
    in_bounds: bool
    in_frame: bool
    confidence: float
    location: tuple[int, int]
    template_name: str
    image: np.ndarray
    template_size: tuple[int, int]
    profile: str


def now() -> datetime:
    return datetime.now().astimezone()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(payload)
    temp.replace(path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
            return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not read %s: %s", path, exc)
        return {}


def load_templates() -> dict[str, list[tuple[str, np.ndarray]]]:
    groups: dict[str, list[tuple[str, np.ndarray]]] = {"all": [], "day": [], "night": []}
    for path in sorted(TEMPLATE_DIR.glob("*.jpg")):
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            logging.warning("Ignoring unreadable template %s", path.name)
            continue
        item = (path.name, image)
        groups["all"].append(item)
        lower = path.name.lower()
        if lower.startswith("day_"):
            groups["day"].append(item)
        elif lower.startswith("night_"):
            groups["night"].append(item)
    if not groups["all"]:
        raise RuntimeError("No readable reference templates found")
    return groups


def active_profile(options: dict[str, Any], ha: HomeAssistantClient) -> str:
    entity_id = str(options.get("day_night_entity", "sun.sun")).strip()
    entity = ha.get_state(entity_id) if entity_id else None
    if entity:
        state = str(entity.get("state", "")).lower()
        if state == "above_horizon":
            return "day"
        if state == "below_horizon":
            return "night"
    hour = now().hour
    start = int(options.get("day_start_hour", 6))
    end = int(options.get("night_start_hour", 18))
    return "day" if start <= hour < end else "night"


def analyse_snapshot(
    camera: UniviewCamera,
    options: dict[str, Any],
    templates: dict[str, list[tuple[str, np.ndarray]]],
    profile: str,
) -> MatchResult:
    raw = camera.snapshot(int(options["snapshot_channel"]))
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError("Camera snapshot was not a decodable image")
    processed = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(image)
    candidates = templates.get(profile) or templates["all"]
    best_confidence = -1.0
    best_location = (0, 0)
    best_name = ""
    best_size = (0, 0)
    for name, template in candidates:
        if template.shape[0] > processed.shape[0] or template.shape[1] > processed.shape[1]:
            continue
        result = cv2.matchTemplate(processed, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(result)
        if confidence > best_confidence:
            best_confidence = float(confidence)
            best_location = (int(location[0]), int(location[1]))
            best_name = name
            best_size = (int(template.shape[1]), int(template.shape[0]))
    threshold_key = "day_match_threshold" if profile == "day" else "night_match_threshold"
    threshold = float(options.get(threshold_key, options["match_threshold"]))
    in_frame = best_confidence >= threshold
    x, y = best_location
    in_bounds = in_frame and (
        int(options["acceptable_x_min"]) <= x <= int(options["acceptable_x_max"])
        and int(options["acceptable_y_min"]) <= y <= int(options["acceptable_y_max"])
    )
    return MatchResult(in_bounds, in_frame, best_confidence, best_location, best_name, image, best_size, profile)


def save_diagnostic(result: MatchResult, options: dict[str, Any]) -> None:
    if not options.get("save_diagnostic_image", True):
        return
    image = cv2.cvtColor(result.image, cv2.COLOR_GRAY2BGR)
    x1, y1 = int(options["acceptable_x_min"]), int(options["acceptable_y_min"])
    x2, y2 = int(options["acceptable_x_max"]), int(options["acceptable_y_max"])
    cv2.rectangle(image, (x1, y1), (x2, y2), (255, 255, 255), 2)
    if result.in_frame:
        x, y = result.location
        w, h = result.template_size
        cv2.rectangle(image, (x, y), (x + w, y + h), (160, 160, 160), 2)
    label = f"{result.profile} {result.template_name or 'none'} {result.confidence:.3f} @ {result.location}"
    cv2.putText(image, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = DIAGNOSTIC_PATH.with_suffix(".tmp.jpg")
    cv2.imwrite(str(temp), image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    os.replace(temp, DIAGNOSTIC_PATH)


def entity_numeric_state(ha: HomeAssistantClient, entity_id: str) -> float | None:
    entity = ha.get_state(entity_id) if entity_id else None
    if not entity:
        return None
    try:
        value = float(entity.get("state"))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def ptz_is_busy(ha: HomeAssistantClient, options: dict[str, Any]) -> bool:
    entity_id = str(options.get("ptz_busy_entity", "")).strip()
    if not entity_id:
        return False
    entity = ha.get_state(entity_id)
    if not entity:
        return bool(options.get("ptz_busy_fail_safe", False))
    busy_states = {str(v).strip().lower() for v in options.get("ptz_busy_states", [])}
    return str(entity.get("state", "")).strip().lower() in busy_states


def reset_auto_guard(camera: UniviewCamera, options: dict[str, Any], state: dict[str, Any]) -> None:
    logging.warning("Resetting camera auto-guard")
    camera.set_auto_guard(False, int(options["auto_guard_preset"]), int(options["auto_guard_time_seconds"]))
    time.sleep(1)
    camera.set_auto_guard(True, int(options["auto_guard_preset"]), int(options["auto_guard_time_seconds"]))
    state["ag_reset"] = now().isoformat()


def update_osd(camera: UniviewCamera, options: dict[str, Any], state: dict[str, Any], checked: datetime) -> None:
    if not options.get("update_camera_osd", True):
        return
    data = camera.get_osd(int(options["osd_channel"]))
    values = data.get("InfoOSD", [])
    if len(values) < 4 or not values[3].get("InfoParam"):
        raise RuntimeError("Expected Uniview OSD slot 4 is unavailable")
    guard = parse_dt(state.get("ag_reset"))
    rectified = parse_dt(state.get("rectified"))
    values[3]["InfoParam"][0]["Value"] = "C:{}|G:{}|R:{}".format(
        checked.strftime("%d%b%H:%M"),
        guard.strftime("%d%b%H:%M") if guard else "-------:--",
        rectified.strftime("%d%b%H:%M") if rectified else "-------:--",
    )
    camera.set_osd(int(options["osd_channel"]), data)


def due(last_value: Any, minutes: int, reference: datetime) -> bool:
    last = parse_dt(last_value)
    return last is None or reference - last >= timedelta(minutes=minutes)


def append_history(result: MatchResult, temperature: float | None, options: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    exists = HISTORY_PATH.exists()
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(["timestamp", "x", "y", "confidence", "in_frame", "in_bounds", "temperature", "profile", "template"])
        writer.writerow([now().isoformat(), result.location[0], result.location[1], f"{result.confidence:.6f}", int(result.in_frame), int(result.in_bounds), "" if temperature is None else temperature, result.profile, result.template_name])
    max_rows = int(options.get("history_max_rows", 10000))
    rows = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    if len(rows) > max_rows + 1:
        HISTORY_PATH.write_text("\n".join([rows[0], *rows[-max_rows:]]) + "\n", encoding="utf-8")


def history_metrics(options: dict[str, Any]) -> dict[str, float | None]:
    result: dict[str, float | None] = {
        "drift_x_per_hour": None,
        "drift_y_per_hour": None,
        "temperature_x_correlation": None,
        "temperature_y_correlation": None,
    }
    if not HISTORY_PATH.exists():
        return result
    cutoff = now() - timedelta(hours=float(options.get("drift_window_hours", 24)))
    samples: list[tuple[float, float, float, float | None]] = []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                stamp = parse_dt(row.get("timestamp"))
                if not stamp or stamp < cutoff or row.get("in_frame") != "1":
                    continue
                temp = float(row["temperature"]) if row.get("temperature") else None
                samples.append((stamp.timestamp() / 3600.0, float(row["x"]), float(row["y"]), temp))
    except Exception as exc:
        logging.warning("Could not calculate history metrics: %s", exc)
        return result
    if len(samples) >= 3:
        t = np.array([s[0] for s in samples], dtype=float)
        x = np.array([s[1] for s in samples], dtype=float)
        y = np.array([s[2] for s in samples], dtype=float)
        result["drift_x_per_hour"] = round(float(np.polyfit(t - t[0], x, 1)[0]), 4)
        result["drift_y_per_hour"] = round(float(np.polyfit(t - t[0], y, 1)[0]), 4)
        temp_samples = [s for s in samples if s[3] is not None]
        if len(temp_samples) >= 5:
            temps = np.array([s[3] for s in temp_samples], dtype=float)
            xs = np.array([s[1] for s in temp_samples], dtype=float)
            ys = np.array([s[2] for s in temp_samples], dtype=float)
            if np.std(temps) > 0 and np.std(xs) > 0:
                result["temperature_x_correlation"] = round(float(np.corrcoef(temps, xs)[0, 1]), 4)
            if np.std(temps) > 0 and np.std(ys) > 0:
                result["temperature_y_correlation"] = round(float(np.corrcoef(temps, ys)[0, 1]), 4)
    return result


def perform_check(camera: UniviewCamera, options: dict[str, Any], templates: dict[str, list[tuple[str, np.ndarray]]], state: dict[str, Any], ha: HomeAssistantClient) -> dict[str, Any]:
    checked = now()
    profile = active_profile(options, ha)
    result = analyse_snapshot(camera, options, templates, profile)
    save_diagnostic(result, options)
    temperature = entity_numeric_state(ha, str(options.get("temperature_entity", "")).strip())
    append_history(result, temperature, options)
    state["checked"] = checked.isoformat()

    failures = int(state.get("consecutive_failures", 0))
    if result.in_bounds:
        state["in_bounds"] = checked.isoformat()
        state["in_frame"] = checked.isoformat()
        failures = 0
        logging.info("PASS: %s matched %.3f in bounds at %s", result.template_name, result.confidence, result.location)
    elif result.in_frame:
        state["in_frame"] = checked.isoformat()
        failures += 1
        logging.warning("DRIFT: %s matched %.3f at %s", result.template_name, result.confidence, result.location)
    else:
        failures += 1
        logging.warning("NOT FOUND: best match %s was %.3f at %s", result.template_name or "none", result.confidence, result.location)
    state["consecutive_failures"] = failures

    if parse_dt(state.get("in_bounds")) is None:
        state["in_bounds"] = checked.isoformat()
    if parse_dt(state.get("in_frame")) is None:
        state["in_frame"] = checked.isoformat()
    out_of_bounds_for = checked - parse_dt(state["in_bounds"])
    out_of_frame_for = checked - parse_dt(state["in_frame"])
    action = "none"

    bounds_due = out_of_bounds_for >= timedelta(minutes=int(options["out_of_bounds_timeout_minutes"]))
    frame_due = out_of_frame_for >= timedelta(minutes=int(options["out_of_frame_timeout_minutes"]))
    debounce_met = failures >= int(options.get("consecutive_failures_before_recovery", 3))
    recovery_due = (frame_due if not result.in_frame else bounds_due) and debounce_met
    busy = ptz_is_busy(ha, options)

    if recovery_due and busy:
        action = "blocked_ptz_busy"
        logging.warning("Recovery required, but PTZ busy interlock is active")
    elif recovery_due:
        guard_due = due(state.get("ag_reset"), int(options["minimum_guard_reset_interval_minutes"]), checked)
        if options.get("auto_guard_enabled", True) and guard_due:
            reset_auto_guard(camera, options, state)
            state["auto_guard_reset_count"] = int(state.get("auto_guard_reset_count", 0)) + 1
            action = "auto_guard_reset"
            settle = int(options["guard_settle_seconds"])
            if settle:
                time.sleep(settle)
            result = analyse_snapshot(camera, options, templates, active_profile(options, ha))
            save_diagnostic(result, options)
            rechecked = now()
            state["checked"] = rechecked.isoformat()
            if result.in_bounds:
                state["in_bounds"] = rechecked.isoformat()
                state["in_frame"] = rechecked.isoformat()
                state["consecutive_failures"] = 0
            elif result.in_frame:
                state["in_frame"] = rechecked.isoformat()

        should_rectify = not result.in_bounds and (result.in_frame or options.get("rectify_when_out_of_frame", True))
        if should_rectify and due(state.get("rectified"), int(options["minimum_rectify_interval_minutes"]), now()):
            logging.error("Invoking Uniview PTZ rectification")
            camera.rectify()
            state["rectified"] = now().isoformat()
            state["rectification_count"] = int(state.get("rectification_count", 0)) + 1
            action = "rectified"

    try:
        update_osd(camera, options, state, checked)
    except Exception as exc:
        logging.error("OSD update failed: %s", exc)

    metrics = history_metrics(options)
    centre_x = (int(options["acceptable_x_min"]) + int(options["acceptable_x_max"])) / 2.0
    centre_y = (int(options["acceptable_y_min"]) + int(options["acceptable_y_max"])) / 2.0
    x_offset = round(result.location[0] - centre_x, 3)
    y_offset = round(result.location[1] - centre_y, 3)
    position_error = round(math.hypot(x_offset, y_offset), 3)
    status = {
        "healthy": True,
        "checked": state.get("checked"),
        "in_bounds": result.in_bounds,
        "in_frame": result.in_frame,
        "confidence": round(result.confidence, 6),
        "x": result.location[0],
        "y": result.location[1],
        "x_offset": x_offset,
        "y_offset": y_offset,
        "position_error": position_error,
        "template": result.template_name,
        "profile": result.profile,
        "temperature": temperature,
        "out_of_bounds_seconds": int(out_of_bounds_for.total_seconds()),
        "out_of_frame_seconds": int(out_of_frame_for.total_seconds()),
        "consecutive_failures": int(state.get("consecutive_failures", 0)),
        "rectification_count": int(state.get("rectification_count", 0)),
        "auto_guard_reset_count": int(state.get("auto_guard_reset_count", 0)),
        "ptz_busy": busy,
        "last_rectified": state.get("rectified"),
        "last_auto_guard_reset": state.get("ag_reset"),
        "last_action": action if action != "none" else state.get("last_action", "none"),
        "last_error": None,
        **metrics,
    }
    if action != "none":
        state["last_action"] = action
    atomic_write_json(STATE_PATH, state)
    atomic_write_json(STATUS_PATH, status)
    return status


def camera_definitions(options: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in (options.get("cameras") or []) if isinstance(c, dict) and c.get("enabled", True)]


def build_camera_clients(options: dict[str, Any]) -> dict[int, UniviewCamera]:
    host = str(options.get("alarm_snapshot_host", "192.168.90.5")).strip()
    port_base = int(options.get("alarm_snapshot_port_base", 30000))
    port_step = int(options.get("alarm_snapshot_port_step", 10))
    username = str(options["username"])
    password = str(options["password"])
    timeout = float(options.get("request_timeout_seconds", 15))
    result: dict[int, UniviewCamera] = {}
    for camera_def in camera_definitions(options):
        try:
            source_id = int(camera_def["source_id"])
        except (KeyError, TypeError, ValueError):
            continue
        port = port_base + (source_id - 1) * port_step
        result[source_id] = UniviewCamera(f"{host}:{port}", username, password, timeout=timeout)
    return result


def probe_camera_controls(
    clients: dict[int, UniviewCamera],
    camera_defs: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    """Probe image controls, respecting an explicit per-camera control channel.

    Dual-sensor cameras can expose more than one valid image channel through the
    same forwarded interface. A successful probe therefore does not prove that
    channel 0 belongs to the logical NVR source represented by this device.
    """
    capabilities: dict[int, dict[str, Any]] = {}
    channels: dict[int, int] = {}
    defs_by_id: dict[int, dict[str, Any]] = {}
    for camera_def in camera_defs:
        try:
            defs_by_id[int(camera_def["source_id"])] = camera_def
        except (KeyError, TypeError, ValueError):
            continue

    for source_id, client in clients.items():
        found: dict[str, Any] = {"day_night": False, "illumination": False}
        selected: int | None = None
        configured_raw = defs_by_id.get(source_id, {}).get("image_control_channel")
        configured: int | None = None
        candidates: list[int] = []
        if configured_raw is not None:
            try:
                configured = int(configured_raw)
                if configured in (0, 1, 2):
                    candidates.append(configured)
                else:
                    logging.warning("D%d has invalid image_control_channel=%r", source_id, configured_raw)
                    configured = None
            except (TypeError, ValueError):
                logging.warning("D%d has invalid image_control_channel=%r", source_id, configured_raw)
                configured = None
        for channel in (0, 1, 2):
            if channel not in candidates:
                candidates.append(channel)

        for channel in candidates:
            try:
                image_caps = client.image_capabilities(channel)
            except Exception as exc:
                logging.debug("D%d image capability probe failed on channel %d: %s", source_id, channel, exc)
                continue

            selected = channel
            try:
                exposure = client.get_exposure(channel)
                day_night = exposure.get("DayNight") if isinstance(exposure, dict) else None
                if isinstance(day_night, dict) and "Mode" in day_night:
                    found["day_night"] = True
                    found["day_night_mode"] = client.get_day_night_mode(channel)
            except Exception as exc:
                logging.debug("D%d day/night probe failed on channel %d: %s", source_id, channel, exc)

            try:
                lamp = client.get_lamp(channel)
                value = lamp.get("Enabled", lamp.get("Enable")) if isinstance(lamp, dict) else None
                if value is not None:
                    found["illumination_enabled"] = bool(value)
                    found["illumination"] = True
                logging.debug(
                    "D%d channel %d LampCtrl=%s",
                    source_id, channel, json.dumps(lamp, sort_keys=True, separators=(",", ":")),
                )
                logging.debug(
                    "D%d channel %d ImageCapabilities=%s",
                    source_id, channel, json.dumps(image_caps, sort_keys=True, separators=(",", ":")),
                )
            except Exception as exc:
                logging.debug("D%d illumination probe failed on channel %d: %s", source_id, channel, exc)

            if found["day_night"] or found["illumination"]:
                break

        if selected is not None:
            channels[source_id] = selected
        capabilities[source_id] = found
        mapping_source = "configured" if configured is not None and selected == configured else "probed"
        logging.info(
            "D%d image controls: channel=%s (%s) day/night=%s illumination=%s",
            source_id, selected, mapping_source, found["day_night"], found["illumination"],
        )
    return capabilities, channels


def capture_camera_snapshot(source_id: int, client: UniviewCamera, preferred_channel: int = 1) -> tuple[bytes, int]:
    last_error: Exception | None = None
    tried: list[int] = []
    for channel in (preferred_channel, 0, 1, 2):
        if channel in tried:
            continue
        tried.append(channel)
        try:
            image = client.snapshot(channel)
            if image.startswith(b"\xff\xd8"):
                return image, channel
            last_error = RuntimeError(f"snapshot channel {channel} did not return JPEG data")
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"No working snapshot channel for D{source_id}: {last_error}")


def persist_manual_snapshot(event_dir: Path, source_id: int, image: bytes, metadata: dict[str, Any]) -> None:
    camera_dir = event_dir / f"D{source_id}"
    camera_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(camera_dir / "last_snapshot.jpg", image)
    atomic_write_json(camera_dir / "last_snapshot.json", metadata)


def rear_zoom_presets(options: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for preset in options.get("rear_zoom_presets", []):
        try:
            name = str(preset["name"]).strip()
            position = float(preset["position"])
        except (KeyError, TypeError, ValueError):
            continue
        if name and 0.0 <= position <= 1.0:
            result[name] = position
    return result


def rear_zoom_status(camera: UniviewCamera, options: dict[str, Any]) -> dict[str, Any]:
    position = camera.get_zoom()
    presets = rear_zoom_presets(options)
    tolerance = float(options.get("rear_zoom_preset_tolerance", 0.006))
    matched = None
    if presets:
        name, error = min(
            ((name, abs(position - target)) for name, target in presets.items()),
            key=lambda item: item[1],
        )
        if error <= tolerance:
            matched = name
    return {
        "zoom": round(position, 9),
        "zoom_percent": round(position * 100.0, 1),
        "preset": matched,
        "checked": now().isoformat(),
        "healthy": True,
        "last_error": None,
    }


def execute_rear_zoom_command(
    command: dict[str, Any],
    rear_camera: UniviewCamera,
    options: dict[str, Any],
) -> dict[str, Any]:
    action = command.get("action")
    current = rear_camera.get_zoom()
    if action == "rear_zoom_preset":
        name = str(command.get("name", ""))
        presets = rear_zoom_presets(options)
        if name not in presets:
            raise ValueError(f"Unknown rear zoom preset {name!r}")
        target = presets[name]
        logging.info("Rear camera zoom preset %s: %.6f -> %.6f", name, current, target)
    elif action in ("rear_zoom_in", "rear_zoom_out"):
        step = float(options.get("rear_zoom_step", 0.05))
        direction = 1.0 if action == "rear_zoom_in" else -1.0
        target = max(0.0, min(1.0, current + direction * step))
        logging.info("Rear camera %s: %.6f -> %.6f", action, current, target)
    elif action == "rear_zoom_set":
        percent = float(command.get("percent"))
        if not 0.0 <= percent <= 100.0:
            raise ValueError(f"Rear zoom percentage must be between 0 and 100, got {percent}")
        target = percent / 100.0
        logging.info("Rear camera absolute zoom %.1f%%: %.6f -> %.6f", percent, current, target)
    else:
        raise ValueError(f"Unsupported rear zoom command {action!r}")

    rear_camera.set_zoom(target)
    tolerance = float(options.get("rear_zoom_preset_tolerance", 0.006))
    deadline = time.monotonic() + float(options.get("rear_zoom_move_timeout_seconds", 8.0))
    position = current
    while time.monotonic() < deadline:
        time.sleep(0.20)
        position = rear_camera.get_zoom()
        if abs(position - target) <= tolerance:
            break
    if abs(position - target) > tolerance:
        logging.warning(
            "Rear camera zoom did not settle at %.6f within timeout; reported %.6f",
            target,
            position,
        )
    return rear_zoom_status(rear_camera, options)


def execute_command(command: dict[str, Any], camera: UniviewCamera, options: dict[str, Any], templates: dict[str, list[tuple[str, np.ndarray]]], state: dict[str, Any], ha: HomeAssistantClient) -> dict[str, Any] | None:
    action = command.get("action")
    if action == "run_check":
        logging.info("Manual position check requested")
        return perform_check(camera, options, templates, state, ha)
    if ptz_is_busy(ha, options):
        logging.warning("Manual %s command blocked by PTZ busy interlock", action)
        state["last_action"] = "blocked_ptz_busy"
        atomic_write_json(STATE_PATH, state)
        return None
    if action == "reset_auto_guard":
        reset_auto_guard(camera, options, state)
        state["auto_guard_reset_count"] = int(state.get("auto_guard_reset_count", 0)) + 1
        state["last_action"] = "manual_auto_guard_reset"
    elif action == "rectify":
        camera.rectify()
        state["rectified"] = now().isoformat()
        state["rectification_count"] = int(state.get("rectification_count", 0)) + 1
        state["last_action"] = "manual_rectified"
    elif action == "preset":
        number = int(command["number"])
        camera.goto_preset(number, int(options.get("ptz_channel", 2)))
        state["last_action"] = f"preset_{number}"
    else:
        return None
    try:
        update_osd(camera, options, state, now())
    except Exception as exc:
        logging.error("OSD update after manual action failed: %s", exc)
    atomic_write_json(STATE_PATH, state)
    status = load_json(STATUS_PATH)
    status.update({
        "healthy": True,
        "checked": now().isoformat(),
        "last_action": state.get("last_action", "none"),
        "last_rectified": state.get("rectified"),
        "last_auto_guard_reset": state.get("ag_reset"),
        "rectification_count": int(state.get("rectification_count", 0)),
        "auto_guard_reset_count": int(state.get("auto_guard_reset_count", 0)),
        "ptz_busy": False,
        "last_error": None,
    })
    atomic_write_json(STATUS_PATH, status)
    return status


def main() -> int:
    options = load_json(OPTIONS_PATH)
    missing = [key for key in ("camera_host", "username", "password") if not options.get(key)]
    if missing:
        raise RuntimeError("Missing required add-on option(s): " + ", ".join(missing))
    logging.basicConfig(level=getattr(logging, str(options.get("log_level", "INFO")).upper(), logging.INFO), format="%(asctime)s %(levelname)s: %(message)s")
    if int(options["acceptable_x_min"]) > int(options["acceptable_x_max"]) or int(options["acceptable_y_min"]) > int(options["acceptable_y_max"]):
        raise RuntimeError("Acceptable-position minimums must not exceed maximums")

    options["addon_version"] = "1.5.2"
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    templates = load_templates()
    state = load_json(STATE_PATH)
    camera = UniviewCamera(str(options["camera_host"]), str(options["username"]), str(options["password"]), float(options["request_timeout_seconds"]))
    rear_camera: UniviewCamera | None = None
    if options.get("rear_zoom_enabled", False):
        rear_host = str(options.get("rear_zoom_host", "")).strip()
        if not rear_host:
            raise RuntimeError("rear_zoom_enabled is true but rear_zoom_host is empty")
        rear_username = str(options.get("rear_zoom_username", "")).strip() or str(options["username"])
        rear_password = str(options.get("rear_zoom_password", "")) or str(options["password"])
        rear_camera = UniviewCamera(
            rear_host,
            rear_username,
            rear_password,
            float(options["request_timeout_seconds"]),
        )
    ha = HomeAssistantClient(float(options["request_timeout_seconds"]))
    commands: queue.Queue[dict[str, Any]] = queue.Queue()
    logging.info("=" * 78)
    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.2")
    logging.info("=" * 78)
    camera_clients = build_camera_clients(options)
    camera_defs = camera_definitions(options)
    camera_caps, camera_control_channels = probe_camera_controls(camera_clients, camera_defs)
    for camera_def in camera_defs:
        source_id = int(camera_def.get("source_id", 0))
        if not bool(camera_def.get("ptz_enabled", False)):
            continue
        client = camera_clients.get(source_id)
        if client is None:
            continue
        try:
            ptz_options = client.get_ptz_configuration_options()
            camera_caps.setdefault(source_id, {})["ptz_options"] = ptz_options
            logging.info("D%d ONVIF PTZ configuration options: %s", source_id, json.dumps(ptz_options, sort_keys=True))
        except Exception as exc:
            logging.warning("D%d ONVIF PTZ configuration-options probe failed: %s", source_id, exc)
        try:
            position = client.get_zoom()
            camera_caps.setdefault(source_id, {})["ptz_zoom"] = True
            camera_caps[source_id]["zoom_percent"] = round(position * 100.0, 1)
            logging.info("D%d ONVIF zoom position: %.6f (%.1f%%)", source_id, position, position * 100.0)
        except Exception as exc:
            camera_caps.setdefault(source_id, {})["ptz_zoom"] = False
            logging.warning("D%d ONVIF zoom-position probe failed: %s", source_id, exc)
    options["_camera_capabilities"] = camera_caps
    mqtt = MQTTDiscovery(options, commands)
    mqtt.start()
    event_dir = PERSIST_DIR / "events"
    history_count = int(options.get("event_history_count", 5))
    event_state = CameraEventState(mqtt, int(options.get("event_hold_seconds", 15)))
    if mqtt.enabled:
        mqtt.connected.wait(timeout=10)
    try:
        camera_caps.setdefault(2, {})["auto_guard_enabled"] = camera.get_auto_guard()
    except Exception as exc:
        logging.warning("D2 auto-guard status probe failed: %s", exc)
        camera_caps.setdefault(2, {})["auto_guard_enabled"] = None
    for source_id, caps in camera_caps.items():
        mqtt.publish_camera_controls(source_id, {
            "day_night_mode": caps.get("day_night_mode"),
            "illumination": caps.get("illumination_enabled") if caps.get("illumination") else None,
            "auto_guard": caps.get("auto_guard_enabled"),
            "zoom_percent": caps.get("zoom_percent") if caps.get("ptz_zoom") else None,
        })
    fallback_sources = {int(v) for v in options.get("alarm_snapshot_fallback_sources", [5])}
    fallback_host = str(options.get("alarm_snapshot_host", "192.168.90.5")).strip()
    fallback_port_base = int(options.get("alarm_snapshot_port_base", 30000))
    fallback_port_step = int(options.get("alarm_snapshot_port_step", 10))
    fallback_channels = []
    for value in (options.get("alarm_snapshot_channel", 1), 0, 1, 2):
        try:
            channel = int(value)
        except (TypeError, ValueError):
            continue
        if channel not in fallback_channels:
            fallback_channels.append(channel)
    fallback_clients: dict[int, UniviewCamera] = {}

    def snapshot_fallback(event: UniviewEvent) -> bytes | None:
        if event.source_id not in fallback_sources or not event.active:
            return None
        client = fallback_clients.get(event.source_id)
        if client is None:
            port = fallback_port_base + (event.source_id - 1) * fallback_port_step
            host = f"{fallback_host}:{port}"
            client = UniviewCamera(host, str(options["username"]), str(options["password"]), float(options["request_timeout_seconds"]))
            fallback_clients[event.source_id] = client
        last_error: Exception | None = None
        for channel in fallback_channels:
            try:
                image = client.snapshot(channel)
                if image.startswith(b"\xff\xd8"):
                    return image
                last_error = RuntimeError(f"snapshot channel {channel} did not return JPEG data")
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return None

    alarm_bridge: AlarmServiceBridge | None = None
    if options.get("alarm_service_enabled", True):
        alarm_bridge = AlarmServiceBridge(
            bind=str(options.get("alarm_service_bind", "0.0.0.0")),
            port=int(options.get("alarm_service_port", 1445)),
            event_dir=event_dir,
            on_event=event_state.handle,
            debug_person_attributes=bool(options.get("debug_person_attributes", False)),
            debug_event_retention=int(options.get("debug_event_retention", 0)),
            event_history_count=history_count,
            allowed_sources=[str(v) for v in options.get("alarm_service_allowed_sources", ["192.168.90.5"])],
            snapshot_fallback=snapshot_fallback,
        )
        alarm_bridge.start()
    interval = int(options["check_interval_seconds"])
    next_check = 0.0
    rear_zoom_poll = int(options.get("rear_zoom_poll_seconds", 10))
    next_rear_zoom_poll = 0.0
    camera_control_poll = int(options.get("camera_control_poll_seconds", 60))
    next_camera_control_poll = time.monotonic() + camera_control_poll
    lamp_watch_sources = {int(v) for v in options.get("lamp_watch_sources", [])}
    lamp_watch_seconds = max(0.2, float(options.get("lamp_watch_seconds", 1.0)))
    next_lamp_watch = 0.0
    last_lamp_watch: dict[int, str] = {}
    ptz_stop_deadlines: dict[int, float] = {}
    ptz_zoom_poll = max(0.2, float(options.get("ptz_zoom_poll_seconds", 1.0)))
    next_ptz_zoom_poll: dict[int, float] = {
        source_id: 0.0 for source_id, caps in camera_caps.items() if caps.get("ptz_zoom")
    }
    logging.info("Started with %d templates; checking every %d seconds", len(templates["all"]), interval)

    try:
        while not stop_requested:
            try:
                command = commands.get(timeout=0.02)
            except queue.Empty:
                command = None
            try:
                event_state.expire()
                if command:
                    if str(command.get("action", "")) == "camera_ptz":
                        source_id = int(command.get("source_id", 0))
                        deferred: list[dict[str, Any]] = []
                        coalesced = 0
                        for _ in range(commands.qsize()):
                            try:
                                candidate = commands.get_nowait()
                            except queue.Empty:
                                break
                            if str(candidate.get("action", "")) == "camera_ptz" and int(candidate.get("source_id", 0)) == source_id:
                                command = candidate
                                coalesced += 1
                            else:
                                deferred.append(candidate)
                        for candidate in deferred:
                            commands.put(candidate)
                        if coalesced:
                            logging.debug("D%d coalesced %d stale PTZ command(s)", source_id, coalesced)
                    action = str(command.get("action", ""))
                    if action.startswith("camera_"):
                        source_id = int(command.get("source_id", 0))
                        client = camera_clients.get(source_id)
                        if client is None:
                            logging.warning("Camera command ignored for unknown/disabled D%d", source_id)
                        elif action == "camera_snapshot":
                            camera_def = next((item for item in camera_defs if int(item.get("source_id", 0)) == source_id), {})
                            snapshot_channel = int(camera_def.get("snapshot_channel", options.get("alarm_snapshot_channel", 1)))
                            image, channel = capture_camera_snapshot(source_id, client, snapshot_channel)
                            stamp = now().isoformat()
                            attrs = {"source_id": source_id, "timestamp": stamp, "event_type": "manual_snapshot", "snapshot_channel": channel}
                            persist_manual_snapshot(event_dir, source_id, image, attrs)
                            mqtt.publish_camera_snapshot(source_id, image, attrs)
                            mqtt.publish_camera_event_message({**attrs, "has_event_image": True, "has_object_crop": False})
                            logging.info("Captured requested snapshot for D%d using channel %d", source_id, channel)
                        elif action == "camera_day_night":
                            channel = camera_control_channels.get(source_id)
                            if channel is None or not camera_caps.get(source_id, {}).get("day_night"):
                                logging.warning("D%d does not expose day/night control", source_id)
                            else:
                                mode = client.set_day_night_mode(channel, str(command.get("mode", "")))
                                camera_caps[source_id]["day_night_mode"] = mode
                                mqtt.publish_camera_controls(source_id, {"day_night_mode": mode, "illumination": camera_caps[source_id].get("illumination_enabled")})
                                logging.info("D%d day/night mode -> %s", source_id, mode)
                        elif action == "camera_illumination":
                            channel = camera_control_channels.get(source_id)
                            if channel is None or not camera_caps.get(source_id, {}).get("illumination"):
                                logging.warning("D%d does not expose illumination control", source_id)
                            else:
                                enabled = client.set_lamp_enabled(channel, bool(command.get("enabled")))
                                camera_caps[source_id]["illumination_enabled"] = enabled
                                mqtt.publish_camera_controls(source_id, {"day_night_mode": camera_caps[source_id].get("day_night_mode"), "illumination": enabled})
                                logging.info("D%d illumination -> %s", source_id, "on" if enabled else "off")
                        elif action == "camera_auto_guard":
                            camera_def = next((item for item in camera_definitions(options) if int(item.get("source_id", 0)) == source_id), None)
                            if source_id != 2 or not camera_def or not bool(camera_def.get("ptz_enabled", False)):
                                logging.warning("D%d does not expose auto-guard control", source_id)
                            else:
                                enabled = bool(command.get("enabled"))
                                camera.set_auto_guard(enabled, int(options["auto_guard_preset"]), int(options["auto_guard_time_seconds"]))
                                camera_caps.setdefault(source_id, {})["auto_guard_enabled"] = enabled
                                mqtt.publish_camera_controls(source_id, {
                                    "day_night_mode": camera_caps[source_id].get("day_night_mode"),
                                    "illumination": camera_caps[source_id].get("illumination_enabled") if camera_caps[source_id].get("illumination") else None,
                                    "auto_guard": enabled,
                                })
                                logging.info("D2 auto-guard -> %s", "on" if enabled else "off")
                        elif action == "camera_zoom_set":
                            camera_def = next((item for item in camera_defs if int(item.get("source_id", 0)) == source_id), None)
                            if not camera_def or not bool(camera_def.get("ptz_enabled", False)) or not camera_caps.get(source_id, {}).get("ptz_zoom"):
                                logging.warning("D%d does not expose absolute bridge zoom control", source_id)
                            else:
                                percent = float(command.get("percent"))
                                if not 0.0 <= percent <= 100.0:
                                    raise ValueError(f"D{source_id} zoom percentage must be between 0 and 100, got {percent}")
                                client.set_zoom(percent / 100.0)
                                time.sleep(0.15)
                                position = client.get_zoom()
                                camera_caps[source_id]["zoom_percent"] = round(position * 100.0, 1)
                                mqtt.publish_camera_controls(source_id, {"zoom_percent": camera_caps[source_id]["zoom_percent"]})
                                next_ptz_zoom_poll[source_id] = time.monotonic() + ptz_zoom_poll
                                logging.info("D%d absolute zoom -> %.1f%% (reported %.1f%%)", source_id, percent, position * 100.0)
                        elif action == "camera_ptz":
                            camera_def = next((item for item in camera_defs if int(item.get("source_id", 0)) == source_id), None)
                            if not camera_def or not bool(camera_def.get("ptz_enabled", False)):
                                logging.warning("D%d does not have bridge PTZ control enabled", source_id)
                            elif bool(command.get("stop", False)):
                                client.stop_move()
                                ptz_stop_deadlines.pop(source_id, None)
                                logging.debug("D%d PTZ stop", source_id)
                            else:
                                pan = max(-1.0, min(1.0, float(command.get("pan", 0.0))))
                                tilt = max(-1.0, min(1.0, float(command.get("tilt", 0.0))))
                                zoom = max(-1.0, min(1.0, float(command.get("zoom", 0.0))))
                                client.continuous_move(pan=pan, tilt=tilt, zoom=zoom)
                                timeout = float(options.get("ptz_safety_timeout_seconds", 3.0))
                                ptz_stop_deadlines[source_id] = time.monotonic() + timeout
                                logging.debug("D%d PTZ velocity pan=%.3f tilt=%.3f zoom=%.3f", source_id, pan, tilt, zoom)
                    elif action.startswith("rear_zoom_"):
                        if rear_camera is None:
                            logging.warning("Rear zoom command ignored because rear camera is disabled")
                        else:
                            rear_status = execute_rear_zoom_command(command, rear_camera, options)
                            mqtt.publish_rear_zoom_status(rear_status)
                            next_rear_zoom_poll = time.monotonic() + rear_zoom_poll
                    else:
                        status = execute_command(command, camera, options, templates, state, ha)
                        if status:
                            mqtt.publish_status(status)
                expired_ptz = [source_id for source_id, deadline in ptz_stop_deadlines.items() if time.monotonic() >= deadline]
                for source_id in expired_ptz:
                    client = camera_clients.get(source_id)
                    ptz_stop_deadlines.pop(source_id, None)
                    if client is not None:
                        try:
                            client.stop_move()
                            logging.warning("D%d PTZ safety timeout: movement stopped", source_id)
                        except Exception as exc:
                            logging.error("D%d PTZ safety stop failed: %s", source_id, exc)

                now_mono = time.monotonic()
                for source_id, deadline in list(next_ptz_zoom_poll.items()):
                    if now_mono < deadline or source_id in ptz_stop_deadlines:
                        continue
                    client = camera_clients.get(source_id)
                    if client is None:
                        continue
                    try:
                        position = client.get_zoom()
                        camera_caps[source_id]["zoom_percent"] = round(position * 100.0, 1)
                        mqtt.publish_camera_controls(source_id, {"zoom_percent": camera_caps[source_id]["zoom_percent"]})
                    except Exception as exc:
                        logging.debug("D%d zoom-position poll failed: %s", source_id, exc)
                    next_ptz_zoom_poll[source_id] = now_mono + ptz_zoom_poll

                if time.monotonic() >= next_check:
                    status = perform_check(camera, options, templates, state, ha)
                    mqtt.publish_status(status)
                    next_check = time.monotonic() + interval
                if lamp_watch_sources and time.monotonic() >= next_lamp_watch:
                    for source_id in sorted(lamp_watch_sources):
                        client = camera_clients.get(source_id)
                        channel = camera_control_channels.get(source_id)
                        if client is None or channel is None:
                            continue
                        try:
                            lamp = client.get_lamp(channel)
                            encoded = json.dumps(lamp, sort_keys=True, separators=(",", ":"))
                            if last_lamp_watch.get(source_id) != encoded:
                                logging.info("D%d LampCtrl changed channel=%d: %s", source_id, channel, encoded)
                                last_lamp_watch[source_id] = encoded
                        except Exception as exc:
                            logging.debug("D%d LampCtrl watch failed: %s", source_id, exc)
                    next_lamp_watch = time.monotonic() + lamp_watch_seconds

                if time.monotonic() >= next_camera_control_poll:
                    for source_id, client in camera_clients.items():
                        channel = camera_control_channels.get(source_id)
                        caps = camera_caps.get(source_id, {})
                        if channel is None:
                            continue
                        try:
                            if caps.get("day_night"):
                                caps["day_night_mode"] = client.get_day_night_mode(channel)
                            if caps.get("illumination"):
                                caps["illumination_enabled"] = client.get_lamp_enabled(channel)
                            if source_id == 2 and bool(next((item for item in camera_definitions(options) if int(item.get("source_id", 0)) == source_id), {}).get("ptz_enabled", False)):
                                try:
                                    caps["auto_guard_enabled"] = camera.get_auto_guard()
                                except Exception as exc:
                                    logging.debug("D2 auto-guard status poll failed: %s", exc)
                            mqtt.publish_camera_controls(source_id, {
                                "day_night_mode": caps.get("day_night_mode"),
                                "illumination": caps.get("illumination_enabled") if caps.get("illumination") else None,
                                "auto_guard": caps.get("auto_guard_enabled"),
                            })
                        except Exception as exc:
                            logging.debug("D%d image-control status poll failed: %s", source_id, exc)
                    next_camera_control_poll = time.monotonic() + camera_control_poll
                if rear_camera is not None and time.monotonic() >= next_rear_zoom_poll:
                    try:
                        mqtt.publish_rear_zoom_status(rear_zoom_status(rear_camera, options))
                    except Exception as exc:
                        logging.warning("Rear camera zoom status failed: %s", exc)
                        mqtt.publish_rear_zoom_status({
                            "healthy": False,
                            "checked": now().isoformat(),
                            "last_error": str(exc),
                        })
                    next_rear_zoom_poll = time.monotonic() + rear_zoom_poll
            except Exception as exc:
                logging.exception("Camera operation failed")
                status = {
                    "healthy": False,
                    "checked": now().isoformat(),
                    "last_error": str(exc),
                    "last_action": state.get("last_action", "error"),
                    "last_rectified": state.get("rectified"),
                    "last_auto_guard_reset": state.get("ag_reset"),
                }
                atomic_write_json(STATUS_PATH, status)
                mqtt.publish_status(status)
                next_check = time.monotonic() + interval
    finally:
        if alarm_bridge is not None:
            alarm_bridge.stop()
        mqtt.stop()
    logging.info("Stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
