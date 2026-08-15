from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]

def sub(path, pattern, repl, count=1):
    p = root / path
    text = p.read_text()
    new, n = re.subn(pattern, repl, text, count=count, flags=re.S)
    if n != count:
        raise SystemExit(f"{path}: expected {count} replacement(s), got {n}: {pattern[:80]}")
    p.write_text(new)

alarm = "uniview_camera_bridge/alarm_service.py"
app = "uniview_camera_bridge/app.py"
mqtt = "uniview_camera_bridge/ha_mqtt.py"

sub(alarm, r'def normalize_event_type\(value: str\) -> tuple\[str, bool \| None\]:.*?\n\ndef parse_position', '''EVENT_TYPES: dict[str, tuple[str, str, bool | None]] = {
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


def parse_position''')

sub(alarm, r'    event_type: str\n    active:', '    event_type: str\n    event_label: str\n    active:')
sub(alarm, r'    bbox: tuple\[int, int, int, int\] \| None = None\n', '    bbox: tuple[int, int, int, int] | None = None\n    detections: list[dict[str, Any]] = field(default_factory=list)\n    primary_detection: int | None = None\n')
sub(alarm, r'    def metadata\(self\) -> dict\[str, Any\]:\n        return \{.*?\n        \}\n\n\nclass AlarmStructureParser:', '''    def metadata(self) -> dict[str, Any]:
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


class AlarmStructureParser:''')

# Both parser entry points now retain canonical label separately from raw type.
sub(alarm, r'event_type, active = normalize_event_type\(raw_type\)', 'event_type, event_label, active = normalize_event_type(raw_type)', count=2)
sub(alarm, r'(event_type=event_type,\n)(            active=active,)', r'\1            event_label=event_label,\n\2', count=2)

sub(alarm, r'        record: dict\[str, Any\] = \{\}\n        object_class: str \| None = None\n        id_key = ""\n        for cls, list_key, id_name in \(.*?                break\n', '''        records_with_class: list[tuple[str, str, dict[str, Any]]] = []
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
''')

sub(alarm, r'        bbox = parse_position\(record.get\("Position"\)\)\n        annotated = self.annotate\(full_jpeg, bbox, object_class, record.get\(id_key\)\)\n', '')
sub(alarm, r'            object_id=record.get\(id_key\) if id_key else None,\n            object_class=object_class,\n            bbox=bbox,\n', '            object_id=object_id,\n            object_class=object_class,\n            bbox=bbox,\n            detections=detections,\n            primary_detection=primary_detection,\n')
sub(alarm, r'            annotated_jpeg=annotated,\n', '')
sub(alarm, r'\n    @staticmethod\n    def annotate\(.*?\n\nclass EventCorrelator:', '\n\nclass EventCorrelator:')
sub(alarm, r'                        event.annotated_jpeg = fallback\n', '')
sub(alarm, r'            event_image = event.annotated_jpeg or event.full_jpeg\n            if event.full_jpeg:\n                atomic_write_bytes\(camera_dir / "last_event_raw.jpg", event.full_jpeg\)\n            if event_image:\n                atomic_write_bytes\(camera_dir / "last_event.jpg", event_image\)\n            if event.crop_jpeg:\n                atomic_write_bytes\(camera_dir / "last_object_crop.jpg", event.crop_jpeg\)\n', '''            # Store one pristine JPEG. Detection geometry and labels are metadata,
            # rendered by the client when requested instead of creating a duplicate.
            event_image = event.full_jpeg
            if event_image:
                atomic_write_bytes(camera_dir / "last_event.jpg", event_image)
''')

# HA state: raw lifecycle remains diagnostic, while Last detection only advances
# on active/unknown events so SmartMotionDetectOff does not replace useful history.
sub(app, r'        state.setdefault\("last_event_type", None\)\n        state.setdefault\("last_raw_event_type", None\)', '        state.setdefault("last_event_type", None)\n        state.setdefault("last_event_label", None)\n        state.setdefault("last_raw_event_type", None)')
sub(app, r'            state\["last_event_type"\] = event.event_type\n            state\["last_raw_event_type"\] = event.raw_type\n            if event.object_class is not None:\n                state\["last_object_class"\] = event.object_class\n            if event.object_id is not None:\n                state\["last_object_id"\] = event.object_id\n            state\["last_event_time"\] = event.timestamp', '''            state["last_raw_event_type"] = event.raw_type
            if event.active is not False:
                state["last_event_type"] = event.event_type
                state["last_event_label"] = event.event_label
                if event.object_class is not None:
                    state["last_object_class"] = event.object_class
                if event.object_id is not None:
                    state["last_object_id"] = event.object_id
                state["last_event_time"] = event.timestamp''')
sub(app, r'        if event.annotated_jpeg:\n            self.mqtt.publish_camera_image\(event.source_id, event.annotated_jpeg, crop=False\)\n        elif event.full_jpeg:\n            self.mqtt.publish_camera_image\(event.source_id, event.full_jpeg, crop=False\)\n        if event.crop_jpeg:\n            self.mqtt.publish_camera_image\(event.source_id, event.crop_jpeg, crop=True\)\n        stream_event = event.metadata\(\)\n        stream_event\["has_event_image"\] = bool\(event.annotated_jpeg or event.full_jpeg\)\n        stream_event\["has_object_crop"\] = bool\(event.crop_jpeg\)', '''        if event.full_jpeg:
            self.mqtt.publish_camera_image(event.source_id, event.full_jpeg, crop=False)
        stream_event = event.metadata()
        stream_event["has_event_image"] = bool(event.full_jpeg)
        stream_event["has_object_crop"] = False''')

# Manual snapshots use the same schema/provenance vocabulary as analytic captures.
sub(app, r'attrs = \{"source_id": source_id, "timestamp": stamp, "event_type": "manual_snapshot", "snapshot_channel": channel\}', 'attrs = {"schema_version": 2, "snapshot_id": f"D{source_id}-{stamp}", "source_id": source_id, "timestamp": stamp, "trigger": {"source": "manual", "event_type": "snapshot", "event_label": "Manual Snapshot"}, "detections": [], "primary_detection": None, "snapshot_channel": channel}')

# MQTT discovery cleanup: add canonical label, retain raw rule diagnostically, and
# deprecate duplicate crop image discovery. Existing singular object fields remain
# temporarily for compatibility while detections[] becomes authoritative metadata.
sub(mqtt, r'\("line_crossing", "Line crossing", None\),', '("line_crossing", "Line crossing", None),\n                ("cross_line", "Cross Line", None),\n                ("enter_area", "Enter Area", None),\n                ("leave_area", "Leave Area", None),')
sub(mqtt, r'            for object_id, name, field in \(\n                \("last_event_type", "Last event type", "last_event_type"\),', '            for object_id, name, field in (\n                ("last_event_type", "Last event type", "last_event_type"),\n                ("last_event_label", "Last detection", "last_event_label"),')
sub(mqtt, r'                \("last_rule", "Last raw event type", "last_raw_event_type"\),\n', '                ("last_rule", "Last raw event type", "last_raw_event_type"),\n')
sub(mqtt, r'\n            self._camera_config\(camera, "image", "last_object_crop", \{.*?\n            \}\)\n', '''
            # last_object_crop is intentionally no longer discovered. The pristine
            # full snapshot plus detections[] can reproduce any ROI without storing
            # or publishing a second lossy image.
''')

# Correct the event state's canonical binary field naming while preserving the old
# line_crossing flag for compatibility during migration.
sub(app, r'        "line_crossing",\n        "intrusion",', '        "line_crossing",\n        "cross_line",\n        "enter_area",\n        "leave_area",\n        "intrusion",')

print("event snapshot metadata patch applied")
