# Uniview Camera Bridge Monitor

The add-on periodically matches a camera snapshot against reference images, records positional history, exposes the result to Home Assistant, and performs guarded recovery actions when drift persists.

## Home Assistant entities

The add-on publishes MQTT Discovery entities for:

- **Camera correctly positioned** binary sensor
- match confidence, detected X and Y, seconds out of bounds, consecutive failed checks and last corrective action
- X/Y drift rate over the configured history window
- current ambient temperature and temperature/X and temperature/Y correlation
- active day/night matching profile and PTZ-busy state
- buttons to run a check, reset auto-guard and invoke rectification
- one button for every configured PTZ preset

A running MQTT broker is required. With the Mosquitto broker add-on, leave `mqtt_host` as `core-mosquitto` and enter credentials for a Home Assistant user that is allowed to use MQTT.

## Recommended configuration additions

```yaml
temperature_entity: sensor.outside_temperature
ptz_busy_entity: binary_sensor.camera_ptz_busy
ptz_busy_states:
  - "on"
  - "busy"
consecutive_failures_before_recovery: 3
ptz_presets:
  - name: Home
    number: 25
  - name: Front gate
    number: 15
  - name: Driveway
    number: 14
mqtt_username: ptz_monitor
mqtt_password: your_mqtt_password
```

Use the actual entity ID of the SHT31 temperature sensor. The PTZ-busy entity is optional. When configured, all automatic recovery and manual movement commands are blocked while its state matches an entry in `ptz_busy_states`. Set `ptz_busy_fail_safe: true` to block movement when Home Assistant cannot read that entity.

## Day and night matching

The add-on uses `sun.sun` by default:

- `above_horizon` selects the day profile and `day_match_threshold`
- `below_horizon` selects the night profile and `night_match_threshold`

When the entity cannot be read, `day_start_hour` and `night_start_hour` provide a fallback schedule.

Existing templates continue to work for both profiles. To provide profile-specific templates, prefix filenames with:

- `day_` for day-only templates
- `night_` for night-only templates

When no template exists for the active profile, the add-on falls back to all bundled templates.

## Drift history and temperature correlation

Each check is appended to `/config/position_history.csv`. The add-on calculates linear X/Y movement in pixels per hour over `drift_window_hours`. When at least five valid temperature samples exist and both variables vary, it also reports Pearson correlation coefficients between temperature and X/Y position.

Correlation is diagnostic rather than proof of causation. A value near `1` or `-1` indicates a strong linear relationship in the retained samples; a value near `0` indicates little linear relationship.

## Recovery debounce

Recovery now requires both:

1. the applicable out-of-bounds or out-of-frame timeout to have elapsed; and
2. at least `consecutive_failures_before_recovery` consecutive failed checks.

A successful in-bounds check resets the failure count.

## Camera OSD

OSD updating remains enabled by default. Slot 4 displays:

```text
C:29Jul12:18|G:29Jul11:42|R:29Jul10:36
```

`C` is the last check, `G` the last auto-guard reset, and `R` the last rectification.

## Persistent files

- `state.json` — recovery state across restarts
- `status.json` — latest published status
- `position_history.csv` — timestamped X/Y, confidence, profile and temperature samples
- `latest_match.jpg` — latest annotated diagnostic frame

## Installation/update

Replace the existing local add-on directory with the new `uniview_ptz_drift` directory, reload the local add-on store, rebuild the add-on, review the new configuration fields, and start it. The existing state files are retained.

## Dashboard-friendly history entities

Version 1.1.3 adds recorder-friendly entities intended for Home Assistant History Graph and Statistics Graph cards:

- Detected X and Y (reported as unknown when the reference is not confidently in frame)
- X and Y position offset from the centre of the configured acceptable rectangle
- Total position error in pixels
- Seconds out of bounds and seconds out of frame
- Last position check, last rectification and last auto-guard reset timestamps
- Cumulative rectification and auto-guard reset counters

The offset and error sensors use the centre of the configured acceptable rectangle as the zero/reference point. Home Assistant Recorder stores their state history automatically unless they have been excluded from Recorder.

## Rear Uniview motorised-zoom camera

The add-on can also expose a separate motorised-zoom Uniview camera as its own Home Assistant MQTT device. This is independent of the front PTZ drift monitoring and uses ONVIF absolute zoom control.

For the IPC2322SB-DZK-I0 on the same forwarded IP at port 30050, configure for example:

```yaml
rear_zoom_enabled: true
rear_zoom_device_name: Rear Uniview Camera
rear_zoom_host: 192.168.90.5:30050
rear_zoom_username: ""
rear_zoom_password: ""
rear_zoom_step: 0.05
rear_zoom_poll_seconds: 10
rear_zoom_presets:
  - name: Wide
    position: 0.0
  - name: Yard
    position: 0.30
  - name: Gate
    position: 0.613333285
  - name: Tele
    position: 1.0
```

An empty `rear_zoom_username` or `rear_zoom_password` falls back to the main camera credentials.

Home Assistant discovery creates a separate **Rear Uniview Camera** device containing:

- **Zoom position** — current absolute zoom position shown as a percentage;
- **Zoom preset** — a select containing the configured named absolute zoom positions;
- **Zoom +** — increases the normalized zoom position by `rear_zoom_step`;
- **Zoom −** — decreases the normalized zoom position by `rear_zoom_step`.

The +/- buttons are not timed continuous-move controls. Each press reads the camera's current ONVIF position and commands a new absolute position, preventing cumulative timing drift. The camera is polled periodically so manual zoom changes made elsewhere are also reflected in Home Assistant.

ONVIF uses a normalized absolute range of `0.0` (fully wide) to `1.0` (fully telephoto). The IPC2322SB-DZK-I0 quantizes some intermediate values internally, so for a carefully chosen preset it is reasonable to use the exact value reported by the Zoom position sensor after positioning the lens.

## Uniview Alarm Service / smart-event bridge (1.3.0)

Version 1.3.0 adds the first camera-bridge milestone without removing the existing PTZ drift and rear-zoom functions.

Point the NVR's **Platform → Alarm Service** configuration at the Home Assistant host IP and the configured `alarm_service_port` (default `1445`). The add-on uses host networking and listens on `0.0.0.0` by default. `alarm_service_allowed_sources` defaults to `192.168.90.5`, so unsolicited POSTs from other LAN hosts are rejected unless you change or clear that list.

The NVR may POST to:

```text
/LAPI/V1.0/System/Event/Notification/Alarm
/LAPI/V1.0/System/Event/Notification/Structure
```

The listener correlates notifications by `RelatedID` where possible. Structure notifications are treated as authoritative rich event records because they already contain classification counts, object records, `Position`, the event frame and object crop.

### Default camera mapping

The default `cameras` configuration maps NVR source IDs to:

```text
D1  Driveway
D2  Front PTZ
D3  Front Static
D4  Front Garden
D5  Rear Wide
D6  Rear Zoom
```

D7 and D8 are present but disabled by default. Adjust `source_id`, `name`, `model` and `enabled` if the NVR channel layout changes.

Existing installations upgraded from 1.2.x also have an internal D1-D6 fallback mapping, so Alarm Service discovery still works before the new `cameras` option is explicitly saved.

### Smart-event Home Assistant entities

Each enabled camera receives a separate MQTT device with:

- Person detected
- Vehicle detected
- Non-motor vehicle detected
- Face detected
- Smart motion
- Motion
- Line crossing
- Intrusion
- Last event type
- Last raw event type
- Last object class
- Last object ID
- Last event time
- Last event image
- Last object crop image

Momentary detections remain ON for `event_hold_seconds` (default 15 seconds) and then clear automatically. Explicit `...Off` notifications clear the corresponding rule state immediately.

### Event images

For Structure notifications the add-on stores, per camera:

```text
/config/events/D<n>/last_event_raw.jpg
/config/events/D<n>/last_event.jpg
/config/events/D<n>/last_object_crop.jpg
/config/events/D<n>/last_event.json
```

`last_event.jpg` is the event-associated full frame annotated with the object bounding box and object class/ID. It is generated from the JPEG carried by the NVR notification; no live video decode or delayed snapshot fetch is required.

The bounding box is converted from Uniview's approximately `0..10000` normalised `Position` coordinate space to the decoded JPEG dimensions.

### Diagnostic person attributes

Set:

```yaml
debug_person_attributes: true
```

to log the raw `AttributeInfo` dictionary and include it in diagnostic event metadata. Current captures contain repeated sentinel-looking values such as `Gender=98`, `AgeRange=98`, and `MaskFlag=255`; these fields are deliberately **not** exposed as normal Home Assistant entities until their meanings and reliability are established.

Set `debug_event_retention` to a non-zero value to retain that many recent raw Structure JSON records and annotated event images per camera under `/config/events/D<n>/debug/`. Leave it at `0` for normal operation.

### Current milestone boundary

Version 1.3.0 establishes the event transport, parsing, image and MQTT foundation. Capability-driven day/night, illumination, autofocus, generic PTZ/zoom discovery, ROI/rule entities and broader camera control migration remain subsequent bridge milestones. Existing D2/D6 controls continue to use the proven paths from earlier releases in the meantime.

## Event snapshot history and alarm-only cameras

Version 1.3.1 retains a configurable rolling snapshot history per camera. `event_history_count` defaults to 5; Home Assistant receives `Last event` plus `Previous event 1` through `Previous event N` MQTT image entities. Files are also retained under the add-on configuration `events/Dx/history` directory.

Some cameras, currently D5 in this installation, emit `HumanShapeDetect` Alarm notifications without a matching Structure notification. `alarm_snapshot_fallback_sources` enables a LAPI snapshot fallback for those source IDs. The default forwarding map uses `alarm_snapshot_host` plus `alarm_snapshot_port_base + (source_id - 1) * alarm_snapshot_port_step`, and tries the preferred snapshot channel followed by the other known channel indices. These fallback images intentionally have no bounding box because the Alarm payload supplies no object position.

## Camera snapshot and image controls (v1.3.3)

Each enabled camera is probed at startup for supported Uniview image controls. Where supported, Home Assistant MQTT discovery creates:

- `Day/night mode` (`Auto`, `Day`, `Night`)
- `Illumination` switch
- `Take snapshot` button
- `Last snapshot` image

The snapshot button is also callable from Home Assistant automations via the normal `button.press` action. Requested snapshots are saved under the add-on data directory as `events/D<n>/last_snapshot.jpg` with JSON metadata, and published to the camera's retained MQTT snapshot topic.

Camera analytics continue to publish `Last event` and `Last object crop`. The bridge also emits a non-retained structured event message on `<mqtt_topic>/events`; this is intended as the hand-off point for a separate security timeline/journal add-on.

Fixed `Previous event N` MQTT image entities were removed in v1.3.3. Existing retained discovery topics from v1.3.1/1.3.2 are deleted automatically when discovery is republished.


### Image control channel

For multi-sensor cameras, `image_control_channel` may be set per camera so day/night and illumination controls target the correct physical sensor. The current D2/D3 dual-lens mapping is D2=0 and D3=1.
