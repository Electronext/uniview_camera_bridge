# Changelog

## 0.1.0-rc1

- Add direct local ONVIF camera discovery using WS-Security UsernameToken authentication.
- Add continuous pan/tilt control and stop semantics compatible with the existing WebRTC PTZ command payload.
- Add PTZ safety timeout and command coalescing for touch/joystick control.
- Track continuous pan/tilt and zoom safety independently, including independent deadlines, so target moves retire only the axes they actually command and a short transition Stop can target one axis without disturbing another.
- Preserve omitted-vs-explicit-zero ContinuousMove semantics through ONVIF serialization; zero-valued replacement axes are transmitted rather than silently omitted.
- Enforce continuous-move safety with independent per-camera watchdogs and ONVIF safety sessions, including retryable Stops and serialization of new movement behind an in-flight safety Stop.
- Make continuous-to-target transitions transactional: validate target payloads first, retain a bounded safety deadline while the replacement request is in flight, and immediately re-arm safety if target transmission fails ambiguously.
- Revalidate watchdog generation and deadline atomically before claiming a safety Stop, preventing stale watchdog observations from cancelling newer movement.
- Make asynchronous PTZ completions generation-safe, including delayed ContinuousMove reconciliation, explicit clearing, and shutdown Stop completion.
- Reconcile every delayed ContinuousMove outcome—success or transport failure—with its watchdog generation. If the outcome overlaps an in-flight Stop, require a post-flight Stop before declaring that generation safe; preserve safety state on failed explicit Stops.
- Preserve command ordering by coalescing only consecutive velocity PTZ updates and treating Stop, target moves, and other-camera commands as barriers.
- Dispatch shutdown Stops independently per camera with bounded waiting.
- Escape dynamic ONVIF SOAP text consistently, including usernames and camera-supplied profile, configuration, and preset tokens.
- Add absolute and relative pan/tilt commands, position feedback, and native preset buttons.
- Keep ONVIF PullPoint events out of the first release pending transport compatibility work.
- Mark this build as a release candidate for controlled Home Assistant hardware validation before merge.
