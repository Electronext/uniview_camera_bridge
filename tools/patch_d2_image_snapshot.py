from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'uniview_camera_bridge'
APP = ROOT / 'app.py'
CONFIG = ROOT / 'config.yaml'
CHANGELOG = ROOT / 'CHANGELOG.md'


def replace_once(path: Path, old: str, new: str):
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise RuntimeError(f'Expected text not found in {path}: {old[:160]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')

# D2/PTZ is LAPI sensor/channel 2 on the dual-lens camera. Channel 1 is the
# companion static sensor (D3), which explains the wrong snapshot/control feedback.
replace_once(CONFIG,
'''  - source_id: 2\n    name: Front PTZ\n    model: IPC9312LFW-AF28-2X4\n    enabled: true\n    image_control_channel: 0\n    ptz_enabled: true\n''',
'''  - source_id: 2\n    name: Front PTZ\n    model: IPC9312LFW-AF28-2X4\n    enabled: true\n    image_control_channel: 2\n    snapshot_channel: 2\n    ptz_enabled: true\n''')
replace_once(CONFIG,
'''    image_control_channel: int(0,2)?\n    ptz_enabled: bool?\n''',
'''    image_control_channel: int(0,2)?\n    snapshot_channel: int(0,2)?\n    ptz_enabled: bool?\n''')

# Manual snapshot should honour a per-camera sensor channel before falling back to
# the global generic snapshot channel.
replace_once(APP,
'''                        elif action == "camera_snapshot":\n                            image, channel = capture_camera_snapshot(source_id, client, int(options.get("alarm_snapshot_channel", 1)))\n''',
'''                        elif action == "camera_snapshot":\n                            camera_def = next((item for item in camera_defs if int(item.get("source_id", 0)) == source_id), {})\n                            snapshot_channel = int(camera_def.get("snapshot_channel", options.get("alarm_snapshot_channel", 1)))\n                            image, channel = capture_camera_snapshot(source_id, client, snapshot_channel)\n''')

replace_once(CONFIG, 'version: 1.5.0\n', 'version: 1.5.1\n')
replace_once(APP, '    options["addon_version"] = "1.5.0"\n', '    options["addon_version"] = "1.5.1"\n')
replace_once(APP, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.0")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.1")\n')

text = CHANGELOG.read_text(encoding='utf-8')
entry = '''## 1.5.1\n\n- Correct D2/front PTZ image-control mapping to LAPI channel 2 on the dual-lens camera.\n- Add optional per-camera `snapshot_channel` and set D2 to channel 2 so manual snapshots no longer return the D3/static sensor image.\n\n'''
if entry not in text:
    CHANGELOG.write_text(entry + text, encoding='utf-8')
