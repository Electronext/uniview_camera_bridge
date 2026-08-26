from __future__ import annotations

import logging
from typing import Any

import app
from ha_mqtt import MQTTDiscovery as BaseMQTTDiscovery
from onvif_adapter import patch_uniview_camera

VERSION = "1.7.0-beta2"


def _enabled(state: dict[str, Any]) -> bool:
    return bool(state.get("rectify_enabled", True))


def _persist_flag(state: dict[str, Any], enabled: bool) -> None:
    state["rectify_enabled"] = bool(enabled)
    app.atomic_write_json(app.STATE_PATH, state)


def _status_with_gate(state: dict[str, Any], *, healthy: bool = True, last_action: str | None = None) -> dict[str, Any]:
    status = app.load_json(app.STATUS_PATH)
    status.update({
        "healthy": healthy,
        "heartbeat": app.now().isoformat(),
        "rectification_enabled": _enabled(state),
        "last_error": None if healthy else status.get("last_error"),
    })
    if last_action is not None:
        status["last_action"] = last_action
    # Keep checked unchanged while disabled: no image-based position check ran.
    app.atomic_write_json(app.STATUS_PATH, status)
    return status


class RectificationMQTTDiscovery(BaseMQTTDiscovery):
    """Add the D2 rectification master switch without changing base MQTT APIs."""

    def __init__(self, options: dict[str, Any], commands: Any):
        options["addon_version"] = VERSION
        super().__init__(options, commands)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        suffix = message.topic.removeprefix(f"{self.base}/command/")
        if suffix == "camera/D2/rectify_enabled":
            payload = message.payload.decode("utf-8", errors="replace").strip().upper()
            if payload not in ("ON", "OFF", "1", "0", "TRUE", "FALSE"):
                logging.warning("Ignoring invalid rectification-enable payload %r", payload)
                return
            self.commands.put({"action": "rectify_enabled", "enabled": payload in ("ON", "1", "TRUE")})
            return
        super()._on_message(client, userdata, message)

    def publish_camera_event_discovery(self) -> None:
        super().publish_camera_event_discovery()
        d2 = next((camera for camera in self._camera_definitions()
                   if int(camera.get("source_id", 0)) == 2 and bool(camera.get("ptz_enabled", False))), None)
        if d2 is None:
            return
        self._camera_config(d2, "switch", "rectification_enabled", {
            "name": "Rectification enabled",
            "state_topic": f"{self.base}/state",
            "value_template": "{{ 'ON' if value_json.rectification_enabled else 'OFF' }}",
            "command_topic": f"{self.base}/command/camera/D2/rectify_enabled",
            "payload_on": "ON", "payload_off": "OFF", "state_on": "ON", "state_off": "OFF",
            "icon": "mdi:image-auto-adjust",
        })


# Patch only ONVIF PTZ methods. Uniview LAPI, Imaging and Alarm Service remain
# the original implementation in uniview.py.
if hasattr(app, "UniviewCamera"):
    patch_uniview_camera(app.UniviewCamera)

_original_perform_check = app.perform_check
_original_execute_command = app.execute_command


def perform_check(camera: Any, options: dict[str, Any], templates: Any, state: dict[str, Any], ha: Any) -> dict[str, Any]:
    state.setdefault("rectify_enabled", True)
    if not _enabled(state):
        logging.debug("Rectification disabled: skipping D2 snapshot/image-processing cycle")
        return _status_with_gate(state, last_action=state.get("last_action", "none"))

    status = _original_perform_check(camera, options, templates, state, ha)
    status["heartbeat"] = app.now().isoformat()
    status["rectification_enabled"] = True
    app.atomic_write_json(app.STATUS_PATH, status)
    return status


def execute_command(command: dict[str, Any], camera: Any, options: dict[str, Any], templates: Any,
                    state: dict[str, Any], ha: Any) -> dict[str, Any] | None:
    action = command.get("action")
    state.setdefault("rectify_enabled", True)

    if action == "rectify_enabled":
        enabled = bool(command.get("enabled"))
        _persist_flag(state, enabled)
        state["last_action"] = "rectification_enabled" if enabled else "rectification_disabled"
        app.atomic_write_json(app.STATE_PATH, state)
        logging.info("D2 rectification/image processing -> %s", "enabled" if enabled else "disabled")
        return _status_with_gate(state, last_action=state["last_action"])

    if action == "rectify" and not _enabled(state):
        logging.warning("Manual D2 rectification blocked because Rectification enabled is OFF")
        state["last_action"] = "rectify_blocked_disabled"
        app.atomic_write_json(app.STATE_PATH, state)
        return _status_with_gate(state, last_action=state["last_action"])

    result = _original_execute_command(command, camera, options, templates, state, ha)
    if result is not None:
        result["heartbeat"] = app.now().isoformat()
        result["rectification_enabled"] = _enabled(state)
        app.atomic_write_json(app.STATUS_PATH, result)
    return result


# All camera event, image-control, proportional PTZ and zoom code remains the
# original 1.7.0-beta1 implementation; only these entry points are wrapped.
app.perform_check = perform_check
app.execute_command = execute_command
app.MQTTDiscovery = RectificationMQTTDiscovery


if __name__ == "__main__":
    raise SystemExit(app.main())
