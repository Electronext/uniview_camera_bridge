from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'uniview_camera_bridge'
APP = ROOT / 'app.py'
CONFIG = ROOT / 'config.yaml'
CHANGELOG = ROOT / 'CHANGELOG.md'


def replace_once(path, old, new):
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise RuntimeError(f'Expected text not found in {path}: {old[:220]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')

replace_once(APP,
'''        if source_id == 2:\n            camera.setdefault("image_control_channel", 2)\n            camera.setdefault("snapshot_channel", 2)\n        elif source_id == 3:\n            camera.setdefault("image_control_channel", 1)\n            camera.setdefault("snapshot_channel", 1)\n''',
'''        if source_id == 2:\n            try:\n                saved_image_channel = int(camera.get("image_control_channel"))\n            except (TypeError, ValueError):\n                saved_image_channel = None\n            if saved_image_channel in (None, 0):\n                if saved_image_channel == 0:\n                    logging.info("Migrating legacy D2 image_control_channel 0 -> 2")\n                camera["image_control_channel"] = 2\n            camera.setdefault("snapshot_channel", 2)\n        elif source_id == 3:\n            camera.setdefault("image_control_channel", 1)\n            camera.setdefault("snapshot_channel", 1)\n''')

replace_once(CONFIG, 'version: 1.5.5\n', 'version: 1.5.6\n')
replace_once(APP, '    options["addon_version"] = "1.5.5"\n', '    options["addon_version"] = "1.5.6"\n')
replace_once(APP, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.5")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.6")\n')

text = CHANGELOG.read_text(encoding='utf-8')
entry = '''## 1.5.6\n\n- Migrate the known stale D2 `image_control_channel: 0` value persisted by older add-on versions to channel 2 at runtime. This fixes D2 Day/Night commands still being sent to the wrong image channel after the 1.5.5 default migration.\n\n'''
if '## 1.5.6\n' not in text:
    CHANGELOG.write_text(entry + text, encoding='utf-8')
