# Tapo Camera Bridge

Home Assistant add-on that exposes local ONVIF PTZ control and position feedback for TP-Link Tapo cameras and other compatible ONVIF PTZ devices.

The initial implementation is intentionally local-only and focused on the PTZ path required by the WebRTC card. It does not require the Tapo cloud service after the camera account/ONVIF credentials have been configured.

## MQTT PTZ command

The WebRTC-compatible continuous PTZ command topic is:

```text
<tapo mqtt_topic>/command/<camera id>/ptz
```

Velocity payload:

```json
{"pan":0.4,"tilt":0.0,"zoom":0.0}
```

Stop payload:

```json
{"stop":true}
```

Pan and tilt use the ONVIF normalized `-1..1` velocity range. A safety timeout stops continuous movement if a stop command is lost.

The bridge also accepts absolute and relative pan/tilt JSON commands on `/absolute` and `/relative`, and publishes native ONVIF preset buttons when the camera advertises presets.

## Current boundary

PullPoint event ingestion is deliberately deferred. The C220 advertises motion, people, line-crossing, tamper, and TP-Link smart-event topics, but its dynamic PullPoint transport needs further compatibility work before it is included here.
