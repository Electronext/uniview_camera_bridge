from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "uniview_camera_bridge"
A = ROOT / "app.py"
CH = ROOT / "CHANGELOG.md"


def repl(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

# Use ONVIF Imaging for normal Auto/Day/Night on every enabled camera.
old = '''        if source_id == 2:\n            try:\n                saved_image_channel = int(camera.get("image_control_channel"))\n            except (TypeError, ValueError):\n                saved_image_channel = None\n            if saved_image_channel in (None, 0):\n                if saved_image_channel == 0:\n                    logging.info("Migrating legacy D2 image_control_channel 0 -> 2")\n                camera["image_control_channel"] = 2\n            camera.setdefault("snapshot_channel", 2)\n            camera.setdefault("day_night_backend", "onvif_imaging")\n            camera.setdefault("video_source_token", "video_source2")\n        elif source_id == 3:\n            camera.setdefault("image_control_channel", 1)\n            camera.setdefault("snapshot_channel", 1)\n            camera.setdefault("day_night_backend", "onvif_imaging")\n            camera.setdefault("video_source_token", "video_source1")\n        else:\n            camera.setdefault("snapshot_channel", 1)\n'''
new = '''        if source_id == 2:\n            try:\n                saved_image_channel = int(camera.get("image_control_channel"))\n            except (TypeError, ValueError):\n                saved_image_channel = None\n            if saved_image_channel in (None, 0):\n                if saved_image_channel == 0:\n                    logging.info("Migrating legacy D2 image_control_channel 0 -> 2")\n                camera["image_control_channel"] = 2\n            camera.setdefault("snapshot_channel", 2)\n            camera["day_night_backend"] = "onvif_imaging"\n            camera["video_source_token"] = "video_source2"\n        elif source_id == 3:\n            camera.setdefault("image_control_channel", 1)\n            camera.setdefault("snapshot_channel", 1)\n            camera["day_night_backend"] = "onvif_imaging"\n            camera["video_source_token"] = "video_source1"\n        else:\n            camera.setdefault("snapshot_channel", 1)\n            camera["day_night_backend"] = "onvif_imaging"\n            camera["video_source_token"] = "video_source"\n'''
repl(A, old, new)

# Rewrite 1.5.13 changelog entry to reflect the now-confirmed fleet-wide behavior.
text = CH.read_text(encoding="utf-8")
start = text.find("## 1.5.13\n")
if start < 0:
    raise RuntimeError("1.5.13 changelog entry not found")
next_heading = text.find("\n## ", start + 1)
if next_heading < 0:
    next_heading = len(text)
entry = '''## 1.5.13\n\n- Switch normal Auto/Day/Night control for all enabled cameras to ONVIF Imaging `GetImagingSettings` / `SetImagingSettings`, matching direct camera-side captures of the NVR/web UI.\n- Map standalone cameras D1/D4/D5/D6 to `video_source`, D2 Front PTZ to `video_source2`, and D3 Front Static to `video_source1`; `IrCutFilter` AUTO/ON/OFF maps to Auto/Day/Night.\n- Keep illumination on the existing Uniview LAPI `LampCtrl` path, which the camera-side captures confirm is still the native mechanism for light control.\n- Do not expose the vendor-specific alarm-input day/night mode at this time; it remains outside the normal ONVIF three-state control and has not been reliable in use.\n\n'''
CH.write_text(text[:start] + entry + text[next_heading:].lstrip("\n"), encoding="utf-8")
