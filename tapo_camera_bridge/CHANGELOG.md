# Changelog

## 0.1.0

- Add direct local ONVIF camera discovery using WS-Security UsernameToken authentication.
- Add continuous pan/tilt control and stop semantics compatible with the existing WebRTC PTZ command payload.
- Add PTZ safety timeout and command coalescing for touch/joystick control.
- Enforce continuous-move safety with independent per-camera watchdogs and ONVIF safety sessions, including retryable Stops and serialization of new movement behind an in-flight safety Stop.
- Make continuous-to-target transitions transactional: validate target payloads before retiring the old watchdog and immediately re-arm safety if target transmission fails ambiguously.
- Preserve command ordering by coalescing only consecutive velocity PTZ updates and treating Stop, target moves, and other-camera commands as barriers.
- Dispatch shutdown Stops independently per camera with bounded waiting.
- Escape dynamic ONVIF SOAP text consistently, including usernames and camera-supplied profile, configuration, and preset tokens.
- Add absolute and relative pan/tilt commands, position feedback, and native preset buttons.
- Keep ONVIF PullPoint events out of the first release pending transport compatibility work.
