from pathlib import Path

root = Path('uniview_camera_bridge')

p = root / 'uniview.py'
s = p.read_text()
old = '''    def set_auto_guard(self, enabled: bool, preset: int, guard_time: int) -> None:
        payload = {
            "Enabled": 1 if enabled else 0,
            "Mode": 0,
            "Param": preset,
            "Time": guard_time,
        }
        self._request("PUT", "/LAPI/V1.0/Channel/2/PTZ/Guard", json=payload)
'''
new = '''    def get_auto_guard(self) -> bool:
        response = self._request("GET", "/LAPI/V1.0/Channel/2/PTZ/Guard")
        data = self._lapi_data(response)
        if not isinstance(data, dict):
            raise RuntimeError("Auto-guard status did not return an object")
        value = data.get("Enabled", data.get("Enable"))
        if value is None:
            raise RuntimeError("Auto-guard status has no Enabled/Enable field")
        return bool(value)

    def set_auto_guard(self, enabled: bool, preset: int, guard_time: int) -> None:
        payload = {
            "Enabled": 1 if enabled else 0,
            "Mode": 0,
            "Param": preset,
            "Time": guard_time,
        }
        response = self._request("PUT", "/LAPI/V1.0/Channel/2/PTZ/Guard", json=payload)
        self._lapi_data(response)
'''
assert old in s
p.write_text(s.replace(old, new, 1))

p = root / 'ha_mqtt.py'
s = p.read_text()
old = '''                elif action == "illumination":
                    self.commands.put({"action": "camera_illumination", "source_id": source_id, "enabled": payload.strip().upper() in ("ON", "1", "TRUE")})
                elif action == "ptz":
'''
new = '''                elif action == "illumination":
                    self.commands.put({"action": "camera_illumination", "source_id": source_id, "enabled": payload.strip().upper() in ("ON", "1", "TRUE")})
                elif action == "auto_guard":
                    self.commands.put({"action": "camera_auto_guard", "source_id": source_id, "enabled": payload.strip().upper() in ("ON", "1", "TRUE")})
                elif action == "ptz":
'''
assert old in s
s = s.replace(old, new, 1)
old = '''            if caps.get("illumination"):
                self._camera_config(camera, "switch", "illumination", {
'''
new = '''            if bool(camera.get("ptz_enabled", False)):
                self._camera_config(camera, "switch", "auto_guard", {
                    "name": "Auto-guard",
                    "state_topic": f"{event_base}/controls",
                    "value_template": "{{ 'ON' if value_json.auto_guard else 'OFF' }}",
                    "command_topic": f"{self.base}/command/camera/D{source_id}/auto_guard",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "state_on": "ON",
                    "state_off": "OFF",
                    "icon": "mdi:shield-home-outline",
                })
            if caps.get("illumination"):
                self._camera_config(camera, "switch", "illumination", {
'''
assert old in s
p.write_text(s.replace(old, new, 1))

p = root / 'app.py'
s = p.read_text()
old = '''    for source_id, caps in camera_caps.items():
        mqtt.publish_camera_controls(source_id, {
            "day_night_mode": caps.get("day_night_mode"),
            "illumination": caps.get("illumination_enabled") if caps.get("illumination") else None,
        })
'''
new = '''    try:
        camera_caps.setdefault(2, {})["auto_guard_enabled"] = camera.get_auto_guard()
    except Exception as exc:
        logging.warning("D2 auto-guard status probe failed: %s", exc)
        camera_caps.setdefault(2, {})["auto_guard_enabled"] = None
    for source_id, caps in camera_caps.items():
        mqtt.publish_camera_controls(source_id, {
            "day_night_mode": caps.get("day_night_mode"),
            "illumination": caps.get("illumination_enabled") if caps.get("illumination") else None,
            "auto_guard": caps.get("auto_guard_enabled"),
        })
'''
assert old in s
s = s.replace(old, new, 1)
old = '''                        elif action == "camera_ptz":
                            camera_def = next((item for item in camera_definitions(options) if int(item.get("source_id", 0)) == source_id), None)
'''
new = '''                        elif action == "camera_auto_guard":
                            camera_def = next((item for item in camera_definitions(options) if int(item.get("source_id", 0)) == source_id), None)
                            if source_id != 2 or not camera_def or not bool(camera_def.get("ptz_enabled", False)):
                                logging.warning("D%d does not expose auto-guard control", source_id)
                            else:
                                enabled = bool(command.get("enabled"))
                                camera.set_auto_guard(enabled, int(options["auto_guard_preset"]), int(options["auto_guard_time_seconds"]))
                                camera_caps.setdefault(source_id, {})["auto_guard_enabled"] = enabled
                                mqtt.publish_camera_controls(source_id, {
                                    "day_night_mode": camera_caps[source_id].get("day_night_mode"),
                                    "illumination": camera_caps[source_id].get("illumination_enabled") if camera_caps[source_id].get("illumination") else None,
                                    "auto_guard": enabled,
                                })
                                logging.info("D2 auto-guard -> %s", "on" if enabled else "off")
                        elif action == "camera_ptz":
                            camera_def = next((item for item in camera_definitions(options) if int(item.get("source_id", 0)) == source_id), None)
'''
assert old in s
s = s.replace(old, new, 1)
old = '''                            mqtt.publish_camera_controls(source_id, {
                                "day_night_mode": caps.get("day_night_mode"),
                                "illumination": caps.get("illumination_enabled") if caps.get("illumination") else None,
                            })
'''
new = '''                            if source_id == 2 and bool(next((item for item in camera_definitions(options) if int(item.get("source_id", 0)) == source_id), {}).get("ptz_enabled", False)):
                                try:
                                    caps["auto_guard_enabled"] = camera.get_auto_guard()
                                except Exception as exc:
                                    logging.debug("D2 auto-guard status poll failed: %s", exc)
                            mqtt.publish_camera_controls(source_id, {
                                "day_night_mode": caps.get("day_night_mode"),
                                "illumination": caps.get("illumination_enabled") if caps.get("illumination") else None,
                                "auto_guard": caps.get("auto_guard_enabled"),
                            })
'''
assert old in s
p.write_text(s.replace(old, new, 1))

p = root / 'CHANGELOG.md'
s = p.read_text()
needle = '- Enabled proportional PTZ for D2 with a configurable fail-safe stop timeout.\n'
line = '- Added D2 auto-guard enable/disable control and state reporting.\n'
if line not in s:
    s = s.replace(needle, needle + line, 1)
p.write_text(s)
