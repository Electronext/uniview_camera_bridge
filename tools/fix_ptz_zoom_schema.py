from pathlib import Path
p = Path('uniview_camera_bridge/config.yaml')
text = p.read_text(encoding='utf-8')
old = '  ptz_zoom_poll_seconds: float(0.2,60.0)\n'
new = '  ptz_zoom_poll_seconds: float(0.2,60.0)?\n'
if old not in text:
    raise SystemExit('expected schema line not found')
p.write_text(text.replace(old, new, 1), encoding='utf-8')
