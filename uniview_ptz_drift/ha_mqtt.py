from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt
import requests


class HomeAssistantClient:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.token = os.environ.get("SUPERVISOR_TOKEN", "")
        self.base_url = "http://supervisor/core/api"

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        if not entity_id or not self.token:
            return None
        try:
            response = requests.get(
                f"{self.base_url}/states/{entity_id}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            value = response.json()
            return value if isinstance(value, dict) else None
        except Exception as exc:
            logging.warning("Could not read Home Assistant entity %s: %s", entity_id, exc)
            return None


class MQTTDiscovery:
    def __init__(self, options: dict[str, Any], commands: queue.Queue[dict[str, Any]]):
        self.options = options
        self.commands = commands
        self.enabled = bool(options.get("mqtt_enabled", True))
        self.base = str(options.get("mqtt_topic", "uniview_ptz_drift")).strip("/")
        self.discovery_prefix = str(options.get("mqtt_discovery_prefix", "homeassistant")).strip("/")
        self.device_id = "uniview_ptz_drift"
        self.client: mqtt.Client | None = None
        self.connected = threading.Event()
        self.discovery_published = False
        self._discovery_thread: threading.Thread | None = None
        self._published_config_count = 0

    def start(self) -> None:
        if not self.enabled:
            logging.info("MQTT publishing disabled")
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.device_id)
        username = str(self.options.get("mqtt_username", ""))
        if username:
            client.username_pw_set(username, str(self.options.get("mqtt_password", "")))
        client.will_set(f"{self.base}/availability", "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        host = str(self.options.get("mqtt_host", "core-mosquitto"))
        port = int(self.options.get("mqtt_port", 1883))
        # Store the client before starting the network loop.  The connect
        # callback may run immediately on a fast LAN; discovery publishing
        # uses self.client and would otherwise be skipped during that race.
        self.client = client
        client.connect_async(host, port, keepalive=60)
        client.loop_start()

    def stop(self) -> None:
        if self.client is None:
            return
        try:
            self.publish_raw(f"{self.base}/availability", "offline", retain=True)
            self.client.disconnect()
            self.client.loop_stop()
        finally:
            self.client = None

    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if reason_code != 0:
            logging.error("MQTT connection failed: %s", reason_code)
            return
        logging.info("Connected to MQTT broker")
        self.connected.set()
        client.subscribe(f"{self.base}/command/#")
        self.publish_raw(f"{self.base}/availability", "online", retain=True)

        # Do not publish discovery from inside Paho's network callback. Some
        # brokers/network combinations do not flush a burst of retained QoS 1
        # packets reliably from the callback itself. Publish on a separate
        # thread, verify each PUBACK, and retry the whole discovery set.
        if self._discovery_thread is None or not self._discovery_thread.is_alive():
            self._discovery_thread = threading.Thread(
                target=self._publish_discovery_with_retries,
                name="mqtt-discovery",
                daemon=True,
            )
            self._discovery_thread.start()

    def _publish_discovery_with_retries(self) -> None:
        # Give the session a moment to settle after CONNACK/subscription setup.
        time.sleep(1.0)
        for attempt in range(1, 4):
            try:
                count = self.publish_discovery()
                self.discovery_published = True
                logging.info(
                    "Published and acknowledged %d Home Assistant MQTT discovery topics",
                    count,
                )
                return
            except Exception as exc:
                self.discovery_published = False
                logging.error(
                    "MQTT discovery publication attempt %d/3 failed: %s",
                    attempt,
                    exc,
                )
                if attempt < 3:
                    time.sleep(3.0 * attempt)

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        suffix = message.topic.removeprefix(f"{self.base}/command/")
        payload = message.payload.decode("utf-8", errors="replace")
        if suffix == "run_check":
            self.commands.put({"action": "run_check"})
        elif suffix == "reset_auto_guard":
            self.commands.put({"action": "reset_auto_guard"})
        elif suffix == "rectify":
            self.commands.put({"action": "rectify"})
        elif suffix == "preset":
            try:
                self.commands.put({"action": "preset", "number": int(payload)})
            except ValueError:
                logging.warning("Ignoring invalid preset MQTT payload %r", payload)
        elif suffix.startswith("camera/D"):
            parts = suffix.split("/")
            if len(parts) >= 3 and parts[1].startswith("D"):
                try:
                    source_id = int(parts[1][1:])
                except ValueError:
                    logging.warning("Ignoring invalid camera command topic %s", suffix)
                    return
                action = parts[2]
                if action == "snapshot":
                    self.commands.put({"action": "camera_snapshot", "source_id": source_id})
                elif action == "day_night":
                    self.commands.put({"action": "camera_day_night", "source_id": source_id, "mode": payload})
                elif action == "illumination":
                    self.commands.put({"action": "camera_illumination", "source_id": source_id, "enabled": payload.strip().upper() in ("ON", "1", "TRUE")})
        elif suffix == "rear_zoom/preset":
            self.commands.put({"action": "rear_zoom_preset", "name": payload})
        elif suffix == "rear_zoom/in":
            self.commands.put({"action": "rear_zoom_in"})
        elif suffix == "rear_zoom/out":
            self.commands.put({"action": "rear_zoom_out"})
        elif suffix == "rear_zoom/set":
            try:
                value = float(payload)
            except ValueError:
                logging.warning("Ignoring invalid rear zoom percentage MQTT payload %r", payload)
            else:
                self.commands.put({"action": "rear_zoom_set", "percent": value})

    @property
    def device(self) -> dict[str, Any]:
        return {
            "identifiers": [self.device_id],
            "name": "Uniview Camera Bridge",
            "manufacturer": "Uniview / custom add-on",
            "model": "Camera bridge / PTZ drift monitor",
            "sw_version": str(self.options.get("addon_version", "1.3.3")),
        }

    @property
    def rear_zoom_device(self) -> dict[str, Any]:
        return {
            "identifiers": ["uniview_rear_zoom"],
            "name": str(self.options.get("rear_zoom_device_name", "Rear Uniview Camera")),
            "manufacturer": "Uniview",
            "model": "IPC2322SB-DZK-I0",
            "sw_version": str(self.options.get("addon_version", "1.3.3")),
        }

    def _rear_config(self, component: str, object_id: str, payload: dict[str, Any]) -> None:
        common = {
            "unique_id": f"uniview_rear_zoom_{object_id}",
            "device": self.rear_zoom_device,
            "availability_topic": f"{self.base}/availability",
        }
        common.update(payload)
        topic = f"{self.discovery_prefix}/{component}/uniview_rear_zoom/{object_id}/config"
        if not self.publish_raw(topic, json.dumps(common), retain=True, wait=True):
            raise RuntimeError(f"broker did not acknowledge discovery topic {topic}")
        self._published_config_count += 1

    def _config(self, component: str, object_id: str, payload: dict[str, Any]) -> None:
        common = {
            "unique_id": f"{self.device_id}_{object_id}",
            "device": self.device,
            "availability_topic": f"{self.base}/availability",
        }
        common.update(payload)
        topic = f"{self.discovery_prefix}/{component}/{self.device_id}/{object_id}/config"
        if not self.publish_raw(topic, json.dumps(common), retain=True, wait=True):
            raise RuntimeError(f"broker did not acknowledge discovery topic {topic}")
        self._published_config_count += 1

    def publish_discovery(self) -> int:
        state_topic = f"{self.base}/state"
        before = self._published_config_count
        sensors = {
            "match_confidence": ("Match confidence", "{{ value_json.confidence }}", None, None, None),
            "detected_x": ("Detected X", "{{ value_json.x if value_json.in_frame else none }}", "px", None, "measurement"),
            "detected_y": ("Detected Y", "{{ value_json.y if value_json.in_frame else none }}", "px", None, "measurement"),
            "x_offset": ("X position offset", "{{ value_json.x_offset if value_json.in_frame else none }}", "px", None, "measurement"),
            "y_offset": ("Y position offset", "{{ value_json.y_offset if value_json.in_frame else none }}", "px", None, "measurement"),
            "position_error": ("Position error", "{{ value_json.position_error if value_json.in_frame else none }}", "px", None, "measurement"),
            "out_of_bounds_seconds": ("Seconds out of bounds", "{{ value_json.out_of_bounds_seconds }}", "s", "duration", "measurement"),
            "out_of_frame_seconds": ("Seconds out of frame", "{{ value_json.out_of_frame_seconds }}", "s", "duration", "measurement"),
            "last_corrective_action": ("Last corrective action", "{{ value_json.last_action }}", None, None, None),
            "consecutive_failures": ("Consecutive failed checks", "{{ value_json.consecutive_failures }}", None, None, "measurement"),
            "rectification_count": ("Rectification count", "{{ value_json.rectification_count }}", None, None, "total_increasing"),
            "auto_guard_reset_count": ("Auto-guard reset count", "{{ value_json.auto_guard_reset_count }}", None, None, "total_increasing"),
            "drift_x_per_hour": ("X drift rate", "{{ value_json.drift_x_per_hour }}", "px/h", None, "measurement"),
            "drift_y_per_hour": ("Y drift rate", "{{ value_json.drift_y_per_hour }}", "px/h", None, "measurement"),
            "ambient_temperature": ("Ambient temperature", "{{ value_json.temperature }}", "°C", "temperature", "measurement"),
            "temperature_x_correlation": ("Temperature/X correlation", "{{ value_json.temperature_x_correlation }}", None, None, "measurement"),
            "temperature_y_correlation": ("Temperature/Y correlation", "{{ value_json.temperature_y_correlation }}", None, None, "measurement"),
            "active_profile": ("Active matching profile", "{{ value_json.profile }}", None, None, None),
        }
        for object_id, (name, template, unit, device_class, state_class) in sensors.items():
            payload: dict[str, Any] = {"name": name, "state_topic": state_topic, "value_template": template}
            if unit:
                payload["unit_of_measurement"] = unit
            if device_class:
                payload["device_class"] = device_class
            if state_class:
                payload["state_class"] = state_class
            self._config("sensor", object_id, payload)

        for object_id, name, field in (
            ("last_check", "Last position check", "checked"),
            ("last_rectified", "Last rectification", "last_rectified"),
            ("last_auto_guard_reset", "Last auto-guard reset", "last_auto_guard_reset"),
        ):
            self._config("sensor", object_id, {
                "name": name,
                "state_topic": state_topic,
                "value_template": "{{ value_json.%s if value_json.%s else none }}" % (field, field),
                "device_class": "timestamp",
            })

        self._config("binary_sensor", "correctly_positioned", {
            "name": "Camera correctly positioned",
            "state_topic": state_topic,
            "value_template": "{{ 'ON' if value_json.in_bounds else 'OFF' }}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "entity_category": "diagnostic",
        })
        self._config("binary_sensor", "ptz_busy", {
            "name": "PTZ busy",
            "state_topic": state_topic,
            "value_template": "{{ 'ON' if value_json.ptz_busy else 'OFF' }}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "entity_category": "diagnostic",
        })

        for object_id, name in (
            ("run_check", "Run position check"),
            ("reset_auto_guard", "Reset auto-guard"),
            ("rectify", "Invoke rectification"),
        ):
            self._config("button", object_id, {
                "name": name,
                "command_topic": f"{self.base}/command/{object_id}",
                "payload_press": "PRESS",
            })

        for preset in self.options.get("ptz_presets", []):
            try:
                number = int(preset["number"])
                name = str(preset["name"])
            except (KeyError, TypeError, ValueError):
                continue
            object_id = f"preset_{number}"
            self._config("button", object_id, {
                "name": f"PTZ preset: {name}",
                "command_topic": f"{self.base}/command/preset",
                "payload_press": str(number),
                "icon": "mdi:camera-control",
            })

        if self.options.get("rear_zoom_enabled", False):
            rear_state = f"{self.base}/rear_zoom/state"
            self._rear_config("sensor", "zoom_position", {
                "name": "Zoom position",
                "state_topic": rear_state,
                "value_template": "{{ value_json.zoom_percent }}",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "icon": "mdi:magnify",
            })
            presets = []
            for preset in self.options.get("rear_zoom_presets", []):
                try:
                    name = str(preset["name"]).strip()
                    position = float(preset["position"])
                except (KeyError, TypeError, ValueError):
                    continue
                if name and 0.0 <= position <= 1.0:
                    presets.append(name)
            if presets:
                self._rear_config("select", "zoom_preset", {
                    "name": "Zoom preset",
                    "state_topic": rear_state,
                    "value_template": "{{ value_json.preset if value_json.preset else none }}",
                    "command_topic": f"{self.base}/command/rear_zoom/preset",
                    "options": presets,
                    "icon": "mdi:camera-control",
                })
                for index, name in enumerate(presets, start=1):
                    self._rear_config("button", f"zoom_preset_{index}", {
                        "name": f"Zoom preset: {name}",
                        "command_topic": f"{self.base}/command/rear_zoom/preset",
                        "payload_press": name,
                        "icon": "mdi:camera-control",
                    })
            self._rear_config("number", "zoom", {
                "name": "Zoom",
                "state_topic": rear_state,
                "value_template": "{{ value_json.zoom_percent }}",
                "command_topic": f"{self.base}/command/rear_zoom/set",
                "min": 0,
                "max": 100,
                "step": 1,
                "mode": "slider",
                "unit_of_measurement": "%",
                "icon": "mdi:magnify",
            })
            self._rear_config("button", "zoom_in", {
                "name": "Zoom +",
                "command_topic": f"{self.base}/command/rear_zoom/in",
                "payload_press": "PRESS",
                "icon": "mdi:magnify-plus-outline",
            })
            self._rear_config("button", "zoom_out", {
                "name": "Zoom −",
                "command_topic": f"{self.base}/command/rear_zoom/out",
                "payload_press": "PRESS",
                "icon": "mdi:magnify-minus-outline",
            })

        if self.options.get("alarm_service_enabled", True):
            self.publish_camera_event_discovery()

        return self._published_config_count - before

    def _camera_definitions(self) -> list[dict[str, Any]]:
        configured = self.options.get("cameras") or [
            {"source_id": 1, "name": "Driveway", "model": "IPC3605SB-ADF16KM-I0", "enabled": True},
            {"source_id": 2, "name": "Front PTZ", "model": "IPC9312LFW-AF28-2X4", "enabled": True},
            {"source_id": 3, "name": "Front Static", "model": "IPC9312LFW-AF28-2X4", "enabled": True},
            {"source_id": 4, "name": "Front Garden", "model": "IPC324SB-DF28K-I0", "enabled": True},
            {"source_id": 5, "name": "Rear Wide", "model": "IPC2K24SE-ADF40KMC-WL-I0", "enabled": True},
            {"source_id": 6, "name": "Rear Zoom", "model": "IPC2322SB-DZK-I0", "enabled": True},
        ]
        result: list[dict[str, Any]] = []
        for item in configured:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            try:
                source_id = int(item.get("source_id"))
            except (TypeError, ValueError):
                continue
            if source_id <= 0:
                continue
            result.append({
                "source_id": source_id,
                "name": str(item.get("name") or f"Camera D{source_id}"),
                "model": str(item.get("model") or "Uniview camera"),
            })
        return result

    def _camera_device(self, camera: dict[str, Any]) -> dict[str, Any]:
        source_id = int(camera["source_id"])
        return {
            "identifiers": [f"uniview_camera_d{source_id}"],
            "name": f"D{source_id} {camera['name']}",
            "manufacturer": "Uniview",
            "model": camera.get("model") or "Uniview camera",
            "via_device": self.device_id,
            "sw_version": str(self.options.get("addon_version", "1.3.3")),
        }

    @staticmethod
    def _entity_slug(value: str) -> str:
        text = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
        return "_".join(part for part in text.split("_") if part)

    def _camera_config(self, camera: dict[str, Any], component: str, object_id: str, payload: dict[str, Any]) -> None:
        source_id = int(camera["source_id"])
        node = f"uniview_camera_d{source_id}"
        camera_slug = self._entity_slug(str(camera["name"]))
        common = {
            "unique_id": f"{node}_{object_id}",
            "device": self._camera_device(camera),
            "availability_topic": f"{self.base}/availability",
            # Home Assistant 2026.4+ uses default_entity_id as the discovery
            # hint. Prefix it explicitly so newly-created entities are stable
            # even if the friendly device name changes later.
            "default_entity_id": f"{component}.d{source_id}_{camera_slug}_{object_id}",
        }
        common.update(payload)
        topic = f"{self.discovery_prefix}/{component}/{node}/{object_id}/config"
        if not self.publish_raw(topic, json.dumps(common), retain=True, wait=True):
            raise RuntimeError(f"broker did not acknowledge discovery topic {topic}")
        self._published_config_count += 1

    def publish_camera_event_discovery(self) -> None:
        for camera in self._camera_definitions():
            source_id = int(camera["source_id"])
            event_base = f"{self.base}/camera/D{source_id}"
            # v1.3.1/1.3.2 exposed fixed Previous event N MQTT image
            # entities. Remove their retained discovery configs as history now
            # belongs to the future timeline/event journal rather than MQTT.
            node = f"uniview_camera_d{source_id}"
            for old_slot in range(1, 20):
                self.publish_raw(
                    f"{self.discovery_prefix}/image/{node}/previous_event_{old_slot}/config",
                    "", retain=True, wait=True,
                )
                self.publish_raw_bytes(f"{event_base}/history/{old_slot + 1}", b"", retain=True)
            state_topic = f"{event_base}/state"
            for object_id, name, device_class in (
                ("person_detected", "Person detected", "occupancy"),
                ("vehicle_detected", "Vehicle detected", None),
                ("non_motor_vehicle_detected", "Non-motor vehicle detected", None),
                ("face_detected", "Face detected", None),
                ("smart_motion", "Smart motion", "motion"),
                ("motion", "Motion", "motion"),
                ("line_crossing", "Line crossing", None),
                ("intrusion", "Intrusion", None),
                ("human_shape_detect", "Human shape detect", "occupancy"),
            ):
                payload: dict[str, Any] = {
                    "name": name,
                    "state_topic": state_topic,
                    "value_template": "{{ 'ON' if value_json.%s else 'OFF' }}" % object_id,
                    "payload_on": "ON",
                    "payload_off": "OFF",
                }
                if device_class:
                    payload["device_class"] = device_class
                self._camera_config(camera, "binary_sensor", object_id, payload)

            for object_id, name, field in (
                ("last_event_type", "Last event type", "last_event_type"),
                ("last_object_class", "Last object class", "last_object_class"),
                ("last_object_id", "Last object ID", "last_object_id"),
                ("last_rule", "Last raw event type", "last_raw_event_type"),
            ):
                self._camera_config(camera, "sensor", object_id, {
                    "name": name,
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.%s if value_json.%s is not none else none }}" % (field, field),
                })
            self._camera_config(camera, "sensor", "last_event_time", {
                "name": "Last event time",
                "state_topic": state_topic,
                "value_template": "{{ value_json.last_event_time if value_json.last_event_time else none }}",
                "device_class": "timestamp",
            })
            self._camera_config(camera, "image", "last_event", {
                "name": "Last event",
                "image_topic": f"{event_base}/image",
                "content_type": "image/jpeg",
                "json_attributes_topic": f"{event_base}/attributes",
            })
            self._camera_config(camera, "image", "last_snapshot", {
                "name": "Last snapshot",
                "image_topic": f"{event_base}/snapshot",
                "content_type": "image/jpeg",
                "json_attributes_topic": f"{event_base}/snapshot_attributes",
            })
            self._camera_config(camera, "button", "take_snapshot", {
                "name": "Take snapshot",
                "command_topic": f"{self.base}/command/camera/D{source_id}/snapshot",
                "payload_press": "PRESS",
                "icon": "mdi:camera",
            })

            caps = (self.options.get("_camera_capabilities") or {}).get(source_id, {})
            if caps.get("day_night"):
                self._camera_config(camera, "select", "day_night_mode", {
                    "name": "Day/night mode",
                    "state_topic": f"{event_base}/controls",
                    "value_template": "{{ value_json.day_night_mode if value_json.day_night_mode else none }}",
                    "command_topic": f"{self.base}/command/camera/D{source_id}/day_night",
                    "options": ["Auto", "Day", "Night"],
                    "icon": "mdi:theme-light-dark",
                })
            if caps.get("illumination"):
                self._camera_config(camera, "switch", "illumination", {
                    "name": "Illumination",
                    "state_topic": f"{event_base}/controls",
                    "value_template": "{{ 'ON' if value_json.illumination else 'OFF' }}",
                    "command_topic": f"{self.base}/command/camera/D{source_id}/illumination",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "state_on": "ON",
                    "state_off": "OFF",
                    "icon": "mdi:lightbulb-night-outline",
                })

            self._camera_config(camera, "image", "last_object_crop", {
                "name": "Last object crop",
                "image_topic": f"{event_base}/crop",
                "content_type": "image/jpeg",
                "json_attributes_topic": f"{event_base}/attributes",
            })

    def publish_camera_event_state(self, source_id: int, status: dict[str, Any], attributes: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        event_base = f"{self.base}/camera/D{int(source_id)}"
        self.publish_raw(f"{event_base}/state", json.dumps(status, separators=(",", ":")), retain=True)
        if attributes is not None:
            self.publish_raw(f"{event_base}/attributes", json.dumps(attributes, separators=(",", ":")), retain=True)

    def publish_camera_image(self, source_id: int, image: bytes, crop: bool = False) -> None:
        if not self.enabled or not image:
            return
        suffix = "crop" if crop else "image"
        self.publish_raw_bytes(f"{self.base}/camera/D{int(source_id)}/{suffix}", image, retain=True)

    def publish_camera_snapshot(self, source_id: int, image: bytes, attributes: dict[str, Any] | None = None) -> None:
        if not self.enabled or not image:
            return
        event_base = f"{self.base}/camera/D{int(source_id)}"
        self.publish_raw_bytes(f"{event_base}/snapshot", image, retain=True)
        if attributes is not None:
            self.publish_raw(f"{event_base}/snapshot_attributes", json.dumps(attributes, separators=(",", ":")), retain=True)

    def publish_camera_controls(self, source_id: int, state: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.publish_raw(f"{self.base}/camera/D{int(source_id)}/controls", json.dumps(state, separators=(",", ":")), retain=True)

    def publish_camera_event_message(self, event: dict[str, Any]) -> None:
        """Publish a non-retained structured event stream for future consumers."""
        if not self.enabled:
            return
        self.publish_raw(f"{self.base}/events", json.dumps(event, separators=(",", ":")), retain=False)

    def publish_raw(
        self,
        topic: str,
        payload: str,
        retain: bool = False,
        wait: bool = False,
    ) -> bool:
        client = self.client
        if client is None:
            logging.error("Cannot publish MQTT topic %s: client is not initialised", topic)
            return False
        info = client.publish(topic, payload, qos=1, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logging.error("MQTT publish failed for %s: rc=%s", topic, info.rc)
            return False
        if wait:
            try:
                info.wait_for_publish(timeout=10.0)
            except Exception as exc:
                logging.error("MQTT PUBACK wait failed for %s: %s", topic, exc)
                return False
            if not info.is_published():
                logging.error("MQTT broker did not acknowledge %s", topic)
                return False
        return True

    def publish_raw_bytes(self, topic: str, payload: bytes, retain: bool = False) -> bool:
        client = self.client
        if client is None:
            logging.error("Cannot publish MQTT topic %s: client is not initialised", topic)
            return False
        info = client.publish(topic, payload, qos=1, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logging.error("MQTT binary publish failed for %s: rc=%s", topic, info.rc)
            return False
        return True

    def publish_status(self, status: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.publish_raw(f"{self.base}/state", json.dumps(status, separators=(",", ":")), retain=True)

    def publish_rear_zoom_status(self, status: dict[str, Any]) -> None:
        if not self.enabled or not self.options.get("rear_zoom_enabled", False):
            return
        self.publish_raw(
            f"{self.base}/rear_zoom/state",
            json.dumps(status, separators=(",", ":")),
            retain=True,
        )
