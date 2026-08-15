from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'uniview_camera_bridge'
H = ROOT / 'ha_mqtt.py'
CH = ROOT / 'CHANGELOG.md'

s = H.read_text(encoding='utf-8')
old = '''            self._camera_config(camera, "image", "last_snapshot", {\n                "name": "Last snapshot",\n                "image_topic": f"{event_base}/snapshot",\n                "content_type": "image/jpeg",\n                "json_attributes_topic": f"{event_base}/snapshot_attributes",\n            })\n            self._camera_config(camera, "button", "take_snapshot", {\n'''
new = '''            self._camera_config(camera, "image", "last_snapshot", {\n                "name": "Last snapshot",\n                "image_topic": f"{event_base}/snapshot",\n                "content_type": "image/jpeg",\n                "json_attributes_topic": f"{event_base}/snapshot_attributes",\n            })\n            self._camera_config(camera, "sensor", "last_snapshot_time", {\n                "name": "Last snapshot time",\n                "state_topic": f"{event_base}/snapshot_attributes",\n                "value_template": "{{ value_json.timestamp if value_json.timestamp else none }}",\n                "device_class": "timestamp",\n            })\n            self._camera_config(camera, "button", "take_snapshot", {\n'''
if old not in s:
    raise RuntimeError('snapshot discovery block not found')
H.write_text(s.replace(old, new, 1), encoding='utf-8')

text = CH.read_text(encoding='utf-8')
needle = '- Preserve the native `text/plain;charset=UTF-8` compact-JSON write format for `/Image/Advanced/Private/Exposure/`.\n'
addition = needle + '- Add a per-camera `Last snapshot time` timestamp sensor sourced from the retained snapshot attributes topic, so its value survives MQTT/discovery reloads.\n'
if addition not in text:
    if needle not in text:
        raise RuntimeError('1.5.11 changelog anchor not found')
    CH.write_text(text.replace(needle, addition, 1), encoding='utf-8')
