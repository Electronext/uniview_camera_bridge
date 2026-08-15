from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

ALARM_PATH = "/LAPI/V1.0/System/Event/Notification/Alarm"
STRUCTURE_PATH = "/LAPI/V1.0/System/Event/Notification/Structure"


def iso_timestamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).astimezone().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now().astimezone().isoformat()


EVENT_TYPES: dict[str, tuple[str, str, bool | None]] = {
    "LineDetectorCrossed": ("cross_line", "Cross Line", True),
    "EnterArea": ("enter_area", "Enter Area", True),
    "LeaveArea": ("leave_area", "Leave Area", True),
    "FieldDetectorObjectsInside": ("intrusion", "Intrusion", True),
    "AccessZone": ("access_zone", "Access Zone", True),
    "LeaveZone": ("leave_zone", "Leave Zone", True),
    "SmartMotionDetectOn": ("smart_motion", "Smart Motion", True),
    "SmartMotionDetection": ("smart_motion", "Smart Motion", True),
    "SmartMotionDetectOff": ("smart_motion", "Smart Motion", False),
    "MotionDetectOn": ("motion", "Motion", True),
    "MotionDetectOff": ("motion", "Motion", False),
    "MotionAlarm": ("motion", "Motion", True),
    "HumanShapeDetect": ("human_shape_detect", "Human Shape", True),
}


def normalize_event_type(value: str) -> tuple[str, str, bool | None]:
    raw = (value or "").strip()
    if raw in EVENT_TYPES:
        return EVENT_TYPES[raw]
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", raw).lower() or "unknown"
    label = re.sub(r"(?<!^)(?=[A-Z])", " ", raw).strip() or "Unknown"
    return snake, label, True if raw else None


def normalize_bbox(bbox: tuple[int, int, int, int] | None) -> list[float] | None:
    if not bbox:
        return None
    return [round(max(0, min(10000, value)) / 10000.0, 6) for value in bbox]


def parse_position(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*;\s*(-?\d+)\s*,\s*(-?\d+)\s*", value)
    if not match:
        return None
    x1, y1, x2, y2 = map(int, match.groups())
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def normalized_bbox_to_pixels(bbox: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    conv_x = lambda v: max(0, min(width - 1, round(v * width / 10000.0)))
    conv_y = lambda v: max(0, min(height - 1, round(v * height / 10000.0)))
    return conv_x(x1), conv_y(y1), conv_x(x2), conv_y(y2)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


@dataclass
class UniviewEvent:
    source_id: int
    source_name: str
    raw_type: str
    event_type: str
    event_label: str
    active: bool | None
    timestamp: str
    timestamp_raw: int | float | None = None
    related_id: str | None = None
    object_id: int | None = None
    object_class: str | None = None
    bbox: tuple[int, int, int, int] | None = None
    detections: list[dict[str, Any]] = field(default_factory=list)
    primary_detection: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    person_attributes: dict[str, Any] | None = None
    full_jpeg: bytes | None = None
    crop_jpeg: bytes | None = None
    annotated_jpeg: bytes | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        event_id = f"D{self.source_id}-{self.related_id or self.object_id or self.timestamp}"
        return {
            "schema_version": 2,
            "event_id": event_id,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "event_type": self.event_type,
            "event_label": self.event_label,
            "raw_event_type": self.raw_type,
            "active": self.active,
            "timestamp": self.timestamp,
            "related_id": self.related_id,
            "object_id": self.object_id,
            "object_class": self.object_class,
            "bbox": normalize_bbox(self.bbox),
            "primary_detection": self.primary_detection,
            "detections": self.detections,
            "person_num": self.counts.get("person", 0),
            "vehicle_num": self.counts.get("vehicle", 0),
            "non_motor_vehicle_num": self.counts.get("non_motor_vehicle", 0),
            "face_num": self.counts.get("face", 0),
        }


class AlarmStructureParser:
    def __init__(self, debug_person_attributes: bool = False):
        self.debug_person_attributes = debug_person_attributes

    def parse_alarm(self, payload: dict[str, Any]) -> UniviewEvent:
        info = payload.get("AlarmInfo") or {}
        raw_type = str(info.get("AlarmType", ""))
        event_type, event_label, active = normalize_event_type(raw_type)
        objects = (payload.get("RelatedObjects") or {}).get("ObjectList") or []
        first = objects[0] if objects and isinstance(objects[0], dict) else {}
        object_type = first.get("ObjectType")
        object_class = {1: "vehicle", 2: "person", 3: "non_motor_vehicle", 4: "face"}.get(object_type)
        if object_class is None and raw_type == "HumanShapeDetect":
            object_class = "person"
        stamp = info.get("TimeStamp")
        return UniviewEvent(
            source_id=int(info.get("AlarmSrcID") or 0),
            source_name="",
            raw_type=raw_type,
            event_type=event_type,
            event_label=event_label,
            active=active,
            timestamp=iso_timestamp(stamp),
            timestamp_raw=stamp,
            related_id=info.get("RelatedID"),
            object_id=first.get("ObjectID"),
            object_class=object_class,
            raw=payload,
        )

    def parse_structure(self, payload: dict[str, Any]) -> UniviewEvent:
        raw_type = str(payload.get("AlarmType", ""))
        event_type, event_label, active = normalize_event_type(raw_type)
        structure = payload.get("StructureInfo") or {}
        obj = structure.get("ObjInfo") or {}
        counts = {
            "person": int(obj.get("PersonNum") or 0),
            "vehicle": int(obj.get("VehicleNum") or 0),
            "non_motor_vehicle": int(obj.get("NonMotorVehicleNum") or 0),
            "face": int(obj.get("FaceNum") or 0),
        }

        records_with_class: list[tuple[str, str, dict[str, Any]]] = []
        for cls, list_key, id_name in (
            ("person", "PersonInfoList", "PersonID"),
            ("vehicle", "VehicleInfoList", "VehicleID"),
            ("non_motor_vehicle", "NonMotorVehicleInfoList", "NonMotorVehicleID"),
            ("face", "FaceInfoList", "FaceID"),
        ):
            for item in obj.get(list_key) or []:
                if isinstance(item, dict):
                    records_with_class.append((cls, id_name, item))

        detections: list[dict[str, Any]] = []
        for cls, id_name, item in records_with_class:
            vendor_bbox = parse_position(item.get("Position"))
            normalized = normalize_bbox(vendor_bbox)
            area = 0.0
            if normalized:
                x1, y1, x2, y2 = normalized
                area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            detections.append({"id": item.get(id_name), "class": cls, "bbox": normalized, "area": round(area, 8)})
        detections.sort(key=lambda item: item.get("area", 0.0), reverse=True)
        for rank, detection in enumerate(detections):
            detection["rank"] = rank
        primary_detection = 0 if detections else None
        primary = detections[0] if detections else {}
        object_class = primary.get("class")
        object_id = primary.get("id")
        primary_box = primary.get("bbox")
        bbox = tuple(round(float(v) * 10000) for v in primary_box) if primary_box else None
        record = records_with_class[0][2] if records_with_class else {}

        images: dict[int, bytes] = {}
        for image in structure.get("ImageInfoList") or []:
            if not isinstance(image, dict):
                continue
            try:
                idx = int(image.get("Index"))
                data = base64.b64decode(image.get("Data") or "", validate=False)
            except (TypeError, ValueError, base64.binascii.Error):
                continue
            if data.startswith(b"\xff\xd8"):
                images[idx] = data

        full_index = record.get("LargePicAttachIndex")
        crop_index = record.get("SmallPicAttachIndex")
        full_jpeg = images.get(int(full_index)) if full_index is not None else None
        crop_jpeg = images.get(int(crop_index)) if crop_index is not None else None
        if full_jpeg is None:
            # Type=1 has been the large event frame in every captured notification.
            for image in structure.get("ImageInfoList") or []:
                if image.get("Type") == 1 and int(image.get("Index") or -1) in images:
                    full_jpeg = images[int(image["Index"])]
                    break

        person_attrs = None
        if object_class == "person" and self.debug_person_attributes:
            attrs = record.get("AttributeInfo")
            person_attrs = dict(attrs) if isinstance(attrs, dict) else None

        stamp = payload.get("TimeStamp")
        return UniviewEvent(
            source_id=int(payload.get("SrcID") or 0),
            source_name=str(payload.get("SrcName") or ""),
            raw_type=raw_type,
            event_type=event_type,
            event_label=event_label,
            active=active,
            timestamp=iso_timestamp(stamp),
            timestamp_raw=stamp,
            related_id=payload.get("RelatedID"),
            object_id=object_id,
            object_class=object_class,
            bbox=bbox,
            detections=detections,
            primary_detection=primary_detection,
            counts=counts,
            person_attributes=person_attrs,
            full_jpeg=full_jpeg,
            crop_jpeg=crop_jpeg,
            raw=payload,
        )


class EventCorrelator:
    def __init__(self, ttl_seconds: int = 30):
        self.ttl_seconds = ttl_seconds
        self._alarms: dict[str, tuple[float, UniviewEvent]] = {}
        self._lock = threading.Lock()

    def remember_alarm(self, event: UniviewEvent) -> None:
        if not event.related_id:
            return
        with self._lock:
            self._expire_locked()
            self._alarms[event.related_id] = (time.monotonic(), event)

    def enrich_structure(self, event: UniviewEvent) -> UniviewEvent:
        if not event.related_id:
            return event
        with self._lock:
            self._expire_locked()
            pair = self._alarms.pop(event.related_id, None)
        if not pair:
            return event
        alarm = pair[1]
        if event.object_id is None:
            event.object_id = alarm.object_id
        if event.object_class is None:
            event.object_class = alarm.object_class
        # Keep the richer Structure naming except for SmartMotionDetection,
        # where Alarm tells us whether it was the On transition.
        if event.event_type == "smart_motion" and alarm.event_type == "smart_motion":
            event.active = alarm.active
        return event

    def _expire_locked(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        for key, (stamp, _) in list(self._alarms.items()):
            if stamp < cutoff:
                del self._alarms[key]


class AlarmServiceBridge:
    def __init__(
        self,
        bind: str,
        port: int,
        event_dir: Path,
        on_event: Callable[[UniviewEvent], None],
        debug_person_attributes: bool = False,
        debug_event_retention: int = 0,
        event_history_count: int = 5,
        allowed_sources: list[str] | None = None,
        snapshot_fallback: Callable[[UniviewEvent], bytes | None] | None = None,
    ):
        self.bind = bind
        self.port = port
        self.event_dir = event_dir
        self.on_event = on_event
        self.parser = AlarmStructureParser(debug_person_attributes)
        self.correlator = EventCorrelator()
        self.debug_person_attributes = debug_person_attributes
        self.debug_event_retention = max(0, int(debug_event_retention))
        self.event_history_count = max(1, int(event_history_count))
        self.snapshot_fallback = snapshot_fallback
        self._persist_lock = threading.Lock()
        self.allowed_sources = {str(v).strip() for v in (allowed_sources or []) if str(v).strip()}
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "UniviewCameraBridge/1.3.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                logging.debug("Alarm Service %s - %s", self.address_string(), fmt % args)

            def do_POST(self) -> None:  # noqa: N802
                peer = self.client_address[0]
                if bridge.allowed_sources and peer not in bridge.allowed_sources:
                    logging.warning("Rejected Alarm Service POST from unconfigured source %s", peer)
                    self.send_error(403)
                    return
                if self.path not in (ALARM_PATH, STRUCTURE_PATH):
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 20 * 1024 * 1024:
                        raise ValueError(f"invalid Content-Length {length}")
                    raw = self.rfile.read(length)
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("JSON root is not an object")
                    bridge.handle(self.path, payload)
                except Exception as exc:
                    logging.exception("Alarm Service request rejected: %s", exc)
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ResponseCode":1}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "18")
                self.end_headers()
                self.wfile.write(b'{"ResponseCode":0}')

        self.server = ThreadingHTTPServer((self.bind, self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="uniview-alarm-service", daemon=True)
        self.thread.start()
        logging.info("Uniview Alarm Service listening on %s:%d", self.bind, self.port)

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=5)
        self.server = None
        self.thread = None

    def handle(self, path: str, payload: dict[str, Any]) -> UniviewEvent:
        if path == ALARM_PATH:
            event = self.parser.parse_alarm(payload)
            self.correlator.remember_alarm(event)
            if event.active and self.snapshot_fallback is not None:
                try:
                    fallback = self.snapshot_fallback(event)
                except Exception as exc:
                    logging.warning("D%d event snapshot fallback failed for %s: %s", event.source_id, event.event_type, exc)
                else:
                    if fallback:
                        event.full_jpeg = fallback
                        self._persist(event)
                        logging.info("Captured fallback event snapshot for D%d %s", event.source_id, event.event_type)
            logging.info("Alarm D%d %s active=%s object=%s related=%s", event.source_id, event.event_type, event.active, event.object_id, event.related_id)
            self.on_event(event)
            return event
        event = self.correlator.enrich_structure(self.parser.parse_structure(payload))
        self._persist(event)
        if self.debug_person_attributes and event.person_attributes:
            logging.info("D%d person %s raw attributes: %s", event.source_id, event.object_id, json.dumps(event.person_attributes, sort_keys=True))
        logging.info(
            "Structure D%d %s %s id=%s bbox=%s related=%s",
            event.source_id,
            event.event_type,
            event.object_class or "object",
            event.object_id,
            event.bbox,
            event.related_id,
        )
        self.on_event(event)
        return event

    def _persist(self, event: UniviewEvent) -> None:
        # Alarm Service uses a threaded HTTP server and some cameras emit several
        # Structure records concurrently. Serialise retention/rotation so another
        # request cannot delete a file while this request is enumerating it.
        with self._persist_lock:
            camera_dir = self.event_dir / f"D{event.source_id}"
            camera_dir.mkdir(parents=True, exist_ok=True)
            # Store one pristine JPEG. Detection geometry and labels are metadata,
            # rendered by the client when requested instead of creating a duplicate.
            event_image = event.full_jpeg
            if event_image:
                atomic_write_bytes(camera_dir / "last_event.jpg", event_image)
            metadata = event.metadata()
            if event.person_attributes is not None:
                metadata["person_attributes"] = event.person_attributes
            atomic_write_bytes(camera_dir / "last_event.json", json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8"))

            if event_image:
                history_dir = camera_dir / "history"
                history_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
                key = re.sub(r"[^A-Za-z0-9_.-]+", "_", event.related_id or str(event.object_id or event.event_type or "event"))
                history_image = history_dir / f"{stamp}_{key}.jpg"
                atomic_write_bytes(history_image, event_image)
                atomic_write_bytes(history_image.with_suffix(".json"), json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8"))
                images = sorted(history_dir.glob("*.jpg"), key=lambda p: p.name, reverse=True)
                for old_image in images[self.event_history_count:]:
                    old_image.unlink(missing_ok=True)
                    old_image.with_suffix(".json").unlink(missing_ok=True)

            if self.debug_event_retention > 0:
                debug_dir = camera_dir / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
                key = re.sub(r"[^A-Za-z0-9_.-]+", "_", event.related_id or str(event.object_id or "event"))
                atomic_write_bytes(debug_dir / f"{stamp}_{key}.json", json.dumps(event.raw, indent=2).encode("utf-8"))
                if event_image:
                    atomic_write_bytes(debug_dir / f"{stamp}_{key}.jpg", event_image)
                # Enumerate by filename while holding the persistence lock; this
                # avoids the prior stat()/unlink race between concurrent POSTs.
                files = sorted(debug_dir.glob("*.json"), key=lambda p: p.name, reverse=True)
                for old_json in files[self.debug_event_retention:]:
                    stem = old_json.stem
                    old_json.unlink(missing_ok=True)
                    (debug_dir / f"{stem}.jpg").unlink(missing_ok=True)

