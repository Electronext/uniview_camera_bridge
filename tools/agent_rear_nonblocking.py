from pathlib import Path

p = Path("uniview_camera_bridge/app.py")
s = p.read_text()
fn = s.index("def execute_rear_zoom_command(")
start = s.index("    rear_camera.set_zoom(target)\n", fn)
ret = "    return rear_zoom_status(rear_camera, options)\n"
end = s.index(ret, start) + len(ret)
replacement = '''    rear_camera.set_zoom(target)
    # Legacy compatibility path only: return immediately so later absolute
    # targets are not queued behind completion of the previous motor move.
    status = rear_zoom_status(rear_camera, options)
    status["target_percent"] = round(target * 100.0, 1)
    return status
'''
p.write_text(s[:start] + replacement + s[end:])
