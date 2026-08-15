from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    s = p.read_text()
    if old not in s:
        raise SystemExit(f'missing expected text in {path}: {old[:80]!r}')
    p.write_text(s.replace(old, new, 1))

# config.yaml
p = Path('uniview_camera_bridge/config.yaml')
s = p.read_text()
s = s.replace('version: 1.6.0', 'version: 1.6.1', 1)
s = s.replace('  event_history_count: 5\n', '  event_history_count: 5\n  event_crop_enabled: true\n  event_crop_history: true\n', 1)
s = s.replace('  event_history_count: int(1,20)\n', '  event_history_count: int(1,20)\n  event_crop_enabled: bool\n  event_crop_history: bool\n', 1)
p.write_text(s)

# app.py: runtime version + crop MQTT publishing
p = Path('uniview_camera_bridge/app.py')
s = p.read_text()
s = s.replace('UNIVIEW CAMERA BRIDGE STARTING - version 1.5.13', 'UNIVIEW CAMERA BRIDGE STARTING - version 1.6.1', 1)
s = s.replace(
    '    def __init__(self, mqtt: MQTTDiscovery, hold_seconds: int = 15):\n        self.mqtt = mqtt\n        self.hold_seconds = max(1, int(hold_seconds))\n',
    '    def __init__(self, mqtt: MQTTDiscovery, hold_seconds: int = 15, event_crop_enabled: bool = True):\n        self.mqtt = mqtt\n        self.hold_seconds = max(1, int(hold_seconds))\n        self.event_crop_enabled = bool(event_crop_enabled)\n', 1)
s = s.replace(
    '            self.mqtt.publish_camera_image(event.source_id, event.full_jpeg, crop=False)\n        stream_event = event.metadata()\n        stream_event["has_event_image"] = bool(event.full_jpeg)\n        stream_event["has_object_crop"] = False\n',
    '            self.mqtt.publish_camera_image(event.source_id, event.full_jpeg, crop=False)\n        if self.event_crop_enabled and event.crop_jpeg:\n            self.mqtt.publish_camera_image(event.source_id, event.crop_jpeg, crop=True)\n        stream_event = event.metadata()\n        stream_event["has_event_image"] = bool(event.full_jpeg)\n        stream_event["has_object_crop"] = bool(self.event_crop_enabled and event.crop_jpeg)\n', 1)
s = s.replace(
    '    event_state = CameraEventState(mqtt, int(options.get("event_hold_seconds", 15)))\n',
    '    event_state = CameraEventState(\n        mqtt,\n        int(options.get("event_hold_seconds", 15)),\n        bool(options.get("event_crop_enabled", True)),\n    )\n', 1)
s = s.replace(
    '            event_history_count=history_count,\n            allowed_sources=',
    '            event_history_count=history_count,\n            event_crop_enabled=bool(options.get("event_crop_enabled", True)),\n            event_crop_history=bool(options.get("event_crop_history", True)),\n            allowed_sources=', 1)
p.write_text(s)

# alarm_service.py: optional persistence of camera-supplied crop
p = Path('uniview_camera_bridge/alarm_service.py')
s = p.read_text()
s = s.replace(
    '        event_history_count: int = 5,\n        allowed_sources: list[str] | None = None,\n',
    '        event_history_count: int = 5,\n        event_crop_enabled: bool = True,\n        event_crop_history: bool = True,\n        allowed_sources: list[str] | None = None,\n', 1)
s = s.replace(
    '        self.event_history_count = max(1, int(event_history_count))\n        self.snapshot_fallback = snapshot_fallback\n',
    '        self.event_history_count = max(1, int(event_history_count))\n        self.event_crop_enabled = bool(event_crop_enabled)\n        self.event_crop_history = bool(event_crop_history)\n        self.snapshot_fallback = snapshot_fallback\n', 1)
s = s.replace(
    '            # Remove legacy duplicate products once this camera next records an event.\n            (camera_dir / "last_event_raw.jpg").unlink(missing_ok=True)\n            (camera_dir / "last_object_crop.jpg").unlink(missing_ok=True)\n',
    '            # Remove the old annotated/raw duplicate; retain the camera-supplied\n            # object crop only when explicitly enabled as an auxiliary thumbnail.\n            (camera_dir / "last_event_raw.jpg").unlink(missing_ok=True)\n            crop_path = camera_dir / "last_object_crop.jpg"\n            if self.event_crop_enabled and event.crop_jpeg:\n                atomic_write_bytes(crop_path, event.crop_jpeg)\n            else:\n                crop_path.unlink(missing_ok=True)\n', 1)
s = s.replace(
    '                atomic_write_bytes(history_image.with_suffix(".json"), json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8"))\n                images = sorted(history_dir.glob("*.jpg"), key=lambda p: p.name, reverse=True)\n',
    '                atomic_write_bytes(history_image.with_suffix(".json"), json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8"))\n                if self.event_crop_enabled and self.event_crop_history and event.crop_jpeg:\n                    atomic_write_bytes(history_image.with_name(history_image.stem + ".crop.jpg"), event.crop_jpeg)\n                images = sorted((p for p in history_dir.glob("*.jpg") if not p.name.endswith(".crop.jpg")), key=lambda p: p.name, reverse=True)\n', 1)
s = s.replace(
    '                    old_image.unlink(missing_ok=True)\n                    old_image.with_suffix(".json").unlink(missing_ok=True)\n',
    '                    old_image.unlink(missing_ok=True)\n                    old_image.with_suffix(".json").unlink(missing_ok=True)\n                    old_image.with_name(old_image.stem + ".crop.jpg").unlink(missing_ok=True)\n', 1)
p.write_text(s)

# ha_mqtt.py: discover crop entity only when enabled
p = Path('uniview_camera_bridge/ha_mqtt.py')
s = p.read_text()
old = '''            self.publish_raw(\n                f"{self.discovery_prefix}/image/{node}/last_object_crop/config",\n                "", retain=True, wait=True,\n            )\n'''
new = '''            if self.options.get("event_crop_enabled", True):\n                self._camera_config(camera, "image", "last_object_crop", {\n                    "name": "Last event crop",\n                    "image_topic": f"{event_base}/crop",\n                    "content_type": "image/jpeg",\n                    "json_attributes_topic": f"{event_base}/attributes",\n                })\n            else:\n                self.publish_raw(\n                    f"{self.discovery_prefix}/image/{node}/last_object_crop/config",\n                    "", retain=True, wait=True,\n                )\n                self.publish_raw_bytes(f"{event_base}/crop", b"", retain=True)\n'''
if old not in s: raise SystemExit('mqtt discovery anchor missing')
s = s.replace(old, new, 1)
s = s.replace(
    '            # last_object_crop is intentionally no longer discovered. The pristine\n            # full snapshot plus detections[] can reproduce any ROI without storing\n            # or publishing a second lossy image.\n\n',
    '', 1)
p.write_text(s)
