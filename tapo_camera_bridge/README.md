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

Pan and tilt use the ONVIF normalized `-1..1` velocity range. Continuous movement is protected by a per-camera safety watchdog. Each camera has an independent watchdog and ONVIF safety session, so a blocked command or Stop request on one camera cannot delay the safety deadline of another camera.

The watchdog is deliberately independent of normal command I/O: if a `ContinuousMove` request is accepted by the camera but its HTTP response stalls or is lost, the watchdog can still issue `Stop` at the configured `ptz_safety_timeout_seconds` deadline. When that blocked `ContinuousMove` eventually completes—successfully or with a transport error—its generation is reconciled. If its earlier safety Stop has already completed, an immediate follow-up Stop is armed. If the earlier Stop is still in flight, the generation is marked so that Stop completion itself schedules a mandatory follow-up Stop. Thus an ambiguous late movement outcome cannot be declared safe merely because an overlapping Stop's response arrives later. Failed safety Stops are retried after `ptz_stop_retry_seconds`. Any new movement-producing command for the same camera—continuous, absolute, relative, or preset—is serialized behind a safety Stop already in flight, preventing an older Stop from racing and cancelling the newer command. Absolute, relative, and preset moves explicitly supersede a pending continuous move. Their payload is validated first; the old continuous-move deadline is retired immediately before transmitting the target command. While the replacement request is in flight, the previous continuous motion remains safety-covered with a short `ptz_transition_safety_seconds` deadline (default 0.5 s), so a stalled 15-second HTTP request cannot leave the camera moving unchecked. If transmission fails ambiguously, safety remains/re-becomes armed immediately because the camera may still be executing the earlier continuous movement. This prevents both stale Stops after a successful target move and loss of the safety net during or after a failed one.

The bridge also accepts absolute and relative pan/tilt JSON commands on `/absolute` and `/relative`, and publishes native ONVIF preset buttons when the camera advertises presets. Velocity-command coalescing applies only to consecutive PTZ updates for the same camera; explicit Stop, absolute/relative/preset movement, and commands for another camera are ordering barriers and are never reordered across.

## ONVIF transport notes

During add-on shutdown, best-effort Stops for all cameras that may still be moving are dispatched independently before the bridge waits for them. `shutdown_stop_wait_seconds` bounds how long shutdown waits for those requests; a slow or unreachable camera cannot prevent Stop from being sent to the others.

The shared ONVIF client XML-escapes camera/user supplied SOAP text such as usernames, profile/configuration tokens and preset tokens. The Tapo bridge creates a separate ONVIF client/session for safety Stops while reusing the discovered service/profile metadata; this is what allows safety traffic to proceed while the normal command session is blocked.

## Current boundary

PullPoint event ingestion is deliberately deferred. The C220 advertises motion, people, line-crossing, tamper, and TP-Link smart-event topics, but its dynamic PullPoint transport needs further compatibility work before it is included here.
