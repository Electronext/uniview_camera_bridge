# Uniview Camera Bridge

A Home Assistant OS add-on that bridges Uniview NVR/camera smart events and camera controls into Home Assistant.

The current release retains the proven PTZ drift monitor and rear motorised-zoom controls while adding the first production Alarm Service event path: object classification, event correlation, bounding boxes, event-associated JPEGs/crops, and per-camera MQTT discovery.

See `DOCS.md` for installation, configuration, current capabilities, and the remaining bridge milestones.

> **Compatibility:** the add-on slug remains `uniview_ptz_drift`, so this release can replace the existing add-on in place without creating a separate Home Assistant add-on identity.
