## 1.6.2

- Make ONVIF PTZ/zoom discovery capability-driven for every enabled camera instead of requiring a hard-coded full-PTZ flag.
- Treat absolute, relative and continuous zoom plus pan/tilt spaces as independent capabilities, allowing zoom-only motorised cameras to use the generic camera control path.
- Make absolute zoom commands non-blocking so a newer target can replace an in-flight target.
- Poll reported zoom position at 0.2 s while a target is outstanding, then return to the normal idle poll interval.
- Keep the legacy `rear_zoom_*` path only as a non-blocking compatibility path; new control should use `camera/Dn/zoom/set`.

## 1.5.13

- Switch normal Auto/Day/Night control for all enabled cameras to ONVIF Imaging `GetImagingSettings` / `SetImagingSettings`, matching direct camera-side captures of the NVR/web UI.
- Map standalone cameras D1/D4/D5/D6 to `video_source`, D2 Front PTZ to `video_source2`, and D3 Front Static to `video_source1`; `IrCutFilter` AUTO/ON/OFF maps to Auto/Day/Night.
- Keep illumination on the existing Uniview LAPI `LampCtrl` path, which the camera-side captures confirm is still the native mechanism for light control.
- Do not expose the vendor-specific alarm-input day/night mode at this time; it remains outside the normal ONVIF three-state control and has not been reliable in use.

## 1.5.12

- Treat the NVR-forwarded camera's bogus HTTP 500 `HTTP <METHOD> method not implemented` SOAP response as the same transient Digest/session fault for PUT as already handled for GET.
- Reset the HTTP session and retry up to three times while preserving the exact request body and headers, allowing D2 private Day/Night writes to renegotiate Digest authentication just like successful LampCtrl/Exposure reads.

## 1.5.11

- D2 Day/Night now uses the exact complete private Exposure payload observed in a fresh native Uniview web-UI capture, changing only `DayNight.Mode`.
- Removed the unsupported `GET /Image/Advanced` path introduced in 1.5.10; `/Image/Advanced/Exposure` remains the read source for current HA state only.
- Preserve the native `text/plain;charset=UTF-8` compact-JSON write format for `/Image/Advanced/Private/Exposure/`.
- Add a per-camera `Last snapshot time` timestamp sensor sourced from the retained snapshot attributes topic, so its value survives MQTT/discovery reloads.

## 1.5.10

- Correct D2 Day/Night read-modify-write source: the private exposure write now mirrors the Exposure object from `/Image/Advanced`, matching the native Uniview web UI, instead of incorrectly echoing `/Image/Advanced/Exposure`.
- Keep the native `text/plain;charset=UTF-8` compact-JSON wire format and change only `DayNight.Mode`.

## 1.5.9

- D2 Day/Night now mirrors the complete current Exposure object, changes only `DayNight.Mode`, and sends the full object back to the native private exposure endpoint.
- Match the native Uniview web UI wire format for that private write: compact JSON carried as `text/plain;charset=UTF-8` rather than `application/json`.

## 1.5.8

- Match the native D2 web UI Day/Night payload exactly: `Mode`, `Sensitivity`, `Time`, `Start`, and `End`. The camera rejects the otherwise valid private exposure write with vendor `UnSupport` when the empty `Start`/`End` fields are omitted.

## 1.5.7

- Send only the `DayNight` sub-object to D2's `Image/Advanced/Private/Exposure/` write endpoint instead of the full generic Exposure payload. The full payload is accepted at HTTP level but rejected by Uniview with `UnSupport`.
- Log the exact Day/Night PUT payload at DEBUG for packet-level comparison.

## 1.5.6

- Migrate the known stale D2 `image_control_channel: 0` value persisted by older add-on versions to channel 2 at runtime. This fixes D2 Day/Night commands still being sent to the wrong image channel after the 1.5.5 default migration.

## 1.5.5

- Apply D2/D3 dual-lens image/snapshot defaults at runtime as well as in `config.yaml`, so existing Home Assistant saved app options created before those fields were added do not silently fall back to snapshot channel 1.
- Read D2 day/night state from the ordinary Exposure endpoint and reserve the packet-capture-proven Private/Exposure endpoint for writes; the private resource is not assumed to support GET.
- Detect day/night support from ImageCapabilities as well as the current Exposure payload, preventing D2 from being disabled merely because a state-read probe is awkward.

## 1.5.3

- Correct D2 dual-lens addressing from native-UI packet captures and a known-good legacy snapshot client: D2 image controls and snapshots use channel 2; D3 uses channel 1.
- Use D2's native `Image/Advanced/Private/Exposure/` endpoint for reliable Auto/Day/Night control.
- Add D2 detailed smart-illumination controls discovered from native-UI traffic: White Light/Infrared type, separate white-light and infrared control-mode selects, white-light level, and infrared near/far levels.
- Slow the diagnostic LampCtrl watcher to 5 seconds and include LAPI HTTP error response bodies in diagnostics.

## 1.5.2

- Correct D2/D3 dual-lens image and snapshot mapping: D2 uses image channel 0 and D3 uses image channel 1; PTZ Channel/2 is a separate numbering domain.
- Add a configurable raw `LampCtrl` change watcher for identifying IR/white/smart illumination fields from native-UI changes.
- Coalesce queued PTZ velocity updates so stop commands are not delayed behind stale movement, reduce command-loop idle latency, and suspend zoom-position polling during active continuous movement.

## 1.5.1

- Correct D2/front PTZ image-control mapping to LAPI channel 2 on the dual-lens camera.
- Add optional per-camera `snapshot_channel` and set D2 to channel 2 so manual snapshots no longer return the D3/static sensor image.

## 1.5.0

- Probe and log ONVIF PTZ `GetConfigurationOptions` spaces/ranges for PTZ-enabled cameras.
- Expose D2/front-PTZ absolute optical zoom as a Home Assistant MQTT `number` slider with actual camera position feedback.
- Poll PTZ zoom position independently (default 1 s) so wheel/button/preset movements are reflected back into Home Assistant.
- Merge partial camera-control state publications so zoom/day-night/illumination/auto-guard fields do not erase one another.
- Preserve `ptz_enabled` in MQTT discovery camera metadata so PTZ-specific entities are discovered reliably.

## 1.4.2

- Select an ONVIF media profile that actually advertises a PTZConfiguration before sending PTZ commands.
- Log discovered ONVIF media profiles and the selected PTZ profile for diagnostics.

## 1.4.1

- Fixed proportional ONVIF PTZ requests to omit unused velocity axes.
- Added detailed ONVIF SOAP fault logging for rejected PTZ commands.

## 1.4.0

- Renamed the add-on slug/source directory to `uniview_camera_bridge`.
- Added direct ONVIF ContinuousMove/Stop support for physical PTZ cameras.
- Added JSON MQTT proportional PTZ command transport at `<mqtt_topic>/command/camera/Dn/ptz`.
- Enabled proportional PTZ for D2 with a configurable fail-safe stop timeout.
- Added D2 auto-guard enable/disable control and state reporting.
- Kept the existing MQTT base topic/device identifiers for Home Assistant compatibility.
- Removed committed Python bytecode cache files.

## 1.3.5

- Fix upgrade validation for existing installations by making per-camera `image_control_channel` optional.
- Preserve explicit D2=0 / D3=1 image-control defaults for new installations while allowing older saved camera entries to load unchanged.
- Replace the legacy `addon_config` map type with the current `app_config` name.

## 1.3.4

- Fix D2/D3 dual-sensor image-control routing: D2 uses image channel 0 and D3 uses image channel 1.
- Add optional per-camera `image_control_channel` configuration; capability probing remains the fallback.
- Add a conspicuous startup separator/version banner to the log.
- Add DEBUG diagnostics for raw `LampCtrl` and image capability responses so multi-mode white/IR illumination can be implemented from observed camera data rather than guessed enum values.

# Changelog

## 1.5.4

- Retry the camera's transient HTTP 500 `HTTP GET method not implemented` fault by resetting Digest/session state; this was causing D2 image-control commands to abort before their PUT.
- Treat an explicitly configured manual snapshot channel as authoritative instead of silently falling back to another lens after a transient failure.
- Keep the packet-capture-proven D2 image/snapshot channel 2 and D3 channel 1 mapping from 1.5.3.

## 1.3.3

- Added per-camera `Take snapshot` buttons and `Last snapshot` image entities for automations and notifications.
- Added startup capability probing for Uniview LAPI day/night and lamp controls; supported cameras receive `Day/night mode` selects and `Illumination` switches.
- Added a non-retained structured event stream at `<mqtt_topic>/events` for future security-timeline consumers.
- Removed MQTT `Previous event N` image slots; MQTT now carries current state/images only.
- Changed event bounding boxes to red, 2 px, with 10% padding around the vendor box. The vendor object crop is unchanged.
- Manual snapshots are persisted as `last_snapshot.jpg/json` per camera with source/timestamp metadata.

## 1.3.0

- Began the transition from the PTZ drift monitor to a general-purpose **Uniview Camera Bridge** while retaining the existing add-on slug and configuration compatibility.
- Added an HTTP Alarm Service listener for Uniview NVR `/Notification/Alarm` and `/Notification/Structure` callbacks.
- Added parsing and correlation of `RelatedID`, object IDs, event types and person/vehicle/non-motor/face counts.
- Normalises Uniview event names such as `LineDetectorCrossed`, `EnterArea`, `SmartMotionDetectOn`, `SmartMotionDetection` and `SmartMotionDetectOff` into stable bridge event types.
- Decodes the event-associated full JPEG and object crop directly from Structure notifications.
- Converts Uniview's approximately 0..10000 `Position` coordinates to image pixels and publishes an annotated event image with the detected object box.
- Adds per-camera MQTT-discovered smart-event entities and `Last event` / `Last object crop` image entities for D1-D6 by default.
- Adds configurable event hold time so momentary person/object/rule sensors return to OFF automatically.
- Adds optional raw person-attribute diagnostic logging and optional rolling debug-event retention; these sentinel-looking attributes are not exposed as normal HA entities.
- Preserves all existing front PTZ drift monitoring, presets, guard/rectification/OSD behaviour, and rear motorised-zoom controls.
- Validated the parser against all 18 requests from the probe v1.9 Alarm Service capture (9 Alarm + 9 Structure records).

## 1.2.3

- Added a Home Assistant MQTT `number` entity named **Zoom** for the rear camera.
- Provides a 0–100% slider with 1% steps and commands ONVIF absolute zoom directly.
- Publishes the camera's actual quantised zoom position back to the slider after movement/polling.
- Existing rear zoom sensor, presets, and Zoom +/- buttons are unchanged.

## 1.2.2

- Added a dedicated Home Assistant button for every configured rear-camera zoom preset, matching the existing front PTZ preset-button workflow.
- Kept the rear zoom preset `select` entity as an alternative control.
- Corrected the reported add-on/device software version to track the packaged release.

## 1.2.1

- Fixed MQTT startup with Paho MQTT 2.x `ReasonCode` objects by using the supported direct success-code comparison instead of coercing the reason code with `int()`.

## 1.2.0

- Added optional support for a separate rear Uniview IPC2322SB-DZK-I0 motorised-zoom camera.
- Added ONVIF absolute zoom position read/write support.
- Added a separate Home Assistant MQTT-discovered rear-camera device.
- Added rear-camera Zoom position sensor, Zoom preset select, and Zoom + / Zoom − buttons.
- Added configurable absolute zoom presets, zoom step size, polling interval, position tolerance, and move timeout.
- Rear camera credentials may be specified independently or inherited from the existing front-camera credentials.

## 1.3.2 - 2026-08-12

- Fixed startup SyntaxError in MQTT camera device naming introduced in 1.3.1.
- Full Python package syntax compilation verified before packaging.

## 1.3.1 - 2026-08-12

- Added configurable rolling event-snapshot history (default 5 images per camera) with Home Assistant MQTT image entities for the previous events.
- Prefixed camera MQTT device names with `D1`-`D6` and publish explicit `default_entity_id` hints such as `image.d1_driveway_last_event`.
- Reduced annotated bounding-box thickness from 3 px to 2 px.
- Added alarm-only snapshot fallback support, enabled for D5 by default. `HumanShapeDetect` now captures a current LAPI JPEG and classifies the event as a person detection when no Structure notification/image is supplied.
- Added configurable snapshot fallback host/port mapping and automatic channel fallback probing.
- Fixed a concurrent debug-retention cleanup race that could raise `FileNotFoundError` while multiple Structure notifications arrived together.
