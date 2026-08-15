from pathlib import Path
import re
root = Path(__file__).resolve().parents[1]

def sub(path, pattern, repl, count=1):
    p=root/path; text=p.read_text(); new,n=re.subn(pattern,repl,text,count=count,flags=re.S)
    if n!=count: raise SystemExit(f'{path}: expected {count}, got {n}: {pattern[:80]}')
    p.write_text(new)

alarm='uniview_camera_bridge/alarm_service.py'
app='uniview_camera_bridge/app.py'
mqtt='uniview_camera_bridge/ha_mqtt.py'

# No image processing remains in the alarm parser: the vendor JPEG is preserved verbatim.
sub(alarm, r'\nimport cv2\nimport numpy as np\n', '\n')
sub(alarm, r'\n\ndef normalized_bbox_to_pixels\(.*?\n    return conv_x\(x1\), conv_y\(y1\), conv_x\(x2\), conv_y\(y2\)\n', '\n')
sub(alarm, r'EVENT_TYPES: dict\[str, tuple\[str, str, bool \| None\]\] = \{', '''DETECTION_CLASS_LABELS = {
    "person": "Person",
    "vehicle": "Vehicle",
    "non_motor_vehicle": "Non-motor Vehicle",
    "face": "Face",
}

EVENT_TYPES: dict[str, tuple[str, str, bool | None]] = {''')
sub(alarm, r'detections.append\(\{"id": item.get\(id_name\), "class": cls, "bbox": normalized, "area": round\(area, 8\)\}\)', 'detections.append({"id": item.get(id_name), "class": cls, "class_label": DETECTION_CLASS_LABELS.get(cls, cls.replace("_", " ").title()), "bbox": normalized, "area": round(area, 8)})')
sub(alarm, r'            "raw_event_type": self.raw_type,\n            "active": self.active,', '''            "raw_event_type": self.raw_type,
            "active": self.active,
            "trigger": {
                "source": "camera_analytics",
                "event_type": self.event_type,
                "event_label": self.event_label,
                "raw_event_type": self.raw_type,
                "related_id": self.related_id,
            },''')
sub(alarm, r'            if event_image:\n                atomic_write_bytes\(camera_dir / "last_event.jpg", event_image\)\n', '''            if event_image:
                atomic_write_bytes(camera_dir / "last_event.jpg", event_image)
            # Remove legacy duplicate products once this camera next records an event.
            (camera_dir / "last_event_raw.jpg").unlink(missing_ok=True)
            (camera_dir / "last_object_crop.jpg").unlink(missing_ok=True)
''')

# Publish every captured analytic frame through the unified Last snapshot entity too.
sub(app, r'        if event.full_jpeg:\n            self.mqtt.publish_camera_image\(event.source_id, event.full_jpeg, crop=False\)\n        stream_event = event.metadata\(\)', '''        if event.full_jpeg:
            metadata = event.metadata()
            if event.person_attributes is not None:
                metadata["person_attributes"] = event.person_attributes
            self.mqtt.publish_camera_snapshot(event.source_id, event.full_jpeg, metadata)
            # Compatibility topic for existing dashboards; Last snapshot is now canonical.
            self.mqtt.publish_camera_image(event.source_id, event.full_jpeg, crop=False)
        stream_event = event.metadata()''')

# Clear retained discovery for the now-redundant crop entity, and classify low-level
# raw/object-ID sensors as diagnostics without removing them abruptly.
sub(mqtt, r'            state_topic = f"\{event_base\}/state"', '''            self.publish_raw(
                f"{self.discovery_prefix}/image/{node}/last_object_crop/config",
                "", retain=True, wait=True,
            )
            state_topic = f"{event_base}/state"''')
sub(mqtt, r'                self._camera_config\(camera, "sensor", object_id, \{\n                    "name": name,\n                    "state_topic": state_topic,\n                    "value_template": "\{\{ value_json.%s if value_json.%s is not none else none \}\}" % \(field, field\),\n                \}\)', '''                payload = {
                    "name": name,
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.%s if value_json.%s is not none else none }}" % (field, field),
                }
                if object_id in ("last_object_id", "last_rule"):
                    payload["entity_category"] = "diagnostic"
                self._camera_config(camera, "sensor", object_id, payload)''')

print('unified snapshot refinements applied')
