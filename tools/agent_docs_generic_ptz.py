from pathlib import Path

# New installs should use per-camera capability discovery. Existing saved
# rear_zoom_enabled options remain usable as a compatibility shim.
cfg = Path("uniview_camera_bridge/config.yaml")
s = cfg.read_text()
s = s.replace("  rear_zoom_enabled: true\n", "  rear_zoom_enabled: false\n", 1)
cfg.write_text(s)

docs = Path("uniview_camera_bridge/DOCS.md")
s = docs.read_text()
start = s.index("## Rear Uniview motorised-zoom camera\n")
end = s.index("## Uniview Alarm Service / smart-event bridge (1.3.0)\n", start)
replacement = '''## Capability-driven PTZ and motorised zoom

From version 1.6.2, PTZ is treated as a set of independently discovered ONVIF capabilities rather than as a camera type or model-specific feature. Every enabled camera is probed through its configured/NVR-forwarded endpoint for the PTZ spaces it actually advertises.

The bridge distinguishes, independently:

- absolute zoom position;
- relative zoom translation;
- continuous zoom velocity;
- absolute pan/tilt position;
- relative pan/tilt translation; and
- continuous pan/tilt velocity.

A motorised varifocal camera that has zoom but no pan/tilt therefore appears naturally as a zoom-capable camera. It does not need a model name, source ID, or special `rear_zoom_*` implementation to enable zoom control. Conversely, a fixed camera that does not advertise the relevant ONVIF spaces does not receive those controls.

When absolute zoom is available, Home Assistant MQTT discovery creates a **Zoom** number on that camera's normal MQTT device. Commands use:

```text
<mqtt_topic>/command/camera/D<n>/zoom/set
```

and the bridge reports the camera's actual position on the normal per-camera controls state topic. Absolute moves are non-blocking: a newer target may replace a target that is still in flight. While a target is outstanding, `ptz_zoom_active_poll_seconds` (default `0.2`) controls the faster position poll used to report physical lens travel; after the target is reached, polling returns to `ptz_zoom_poll_seconds` (default `1.0`).

The old `rear_zoom_*` MQTT device and options remain temporarily available for compatibility with existing dashboards. They are no longer the preferred implementation, and their command path is also non-blocking. New installations default `rear_zoom_enabled` to `false` and should use the camera's discovered per-camera Zoom entity instead.

ONVIF absolute zoom is normalized to `0.0` (fully wide) through `1.0` (fully telephoto). Some cameras quantize intermediate values internally; the reported Home Assistant state is therefore the camera's actual position rather than merely the last requested target.

'''
s = s[:start] + replacement + s[end:]
s = s.replace(
    "Capability-driven day/night, illumination, autofocus, generic PTZ/zoom discovery, ROI/rule entities and broader camera control migration remain subsequent bridge milestones. Existing D2/D6 controls continue to use the proven paths from earlier releases in the meantime.",
    "Capability-driven PTZ/zoom discovery is implemented from version 1.6.2. Further capability-driven migration of remaining vendor-specific controls continues independently; existing proven fallbacks are retained where discovery is not yet reliable.",
)
docs.write_text(s)
