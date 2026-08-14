from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "uniview_camera_bridge"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected text not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---- uniview.py: retain PTZ configuration tokens and query configuration options
p = ROOT / "uniview.py"
replace_once(
    p,
    '        self._onvif_profiles: list[str] | None = None\n',
    '        self._onvif_profiles: list[str] | None = None\n        self._onvif_profile_configs: dict[str, str | None] = {}\n',
)
replace_once(
    p,
    '        self._onvif_ptz_url = ptz_url\n        self._onvif_profiles = profiles\n        return ptz_url, profiles\n\n    def get_zoom(self, profile: str | None = None) -> float:\n',
    '''        self._onvif_ptz_url = ptz_url\n        self._onvif_profiles = profiles\n        self._onvif_profile_configs = {token: config for token, _has_ptz, config in profile_info}\n        return ptz_url, profiles\n\n    @staticmethod\n    def _range_values(element: ET.Element) -> dict[str, float] | None:\n        values: dict[str, float] = {}\n        for child in element:\n            name = _localname(child.tag)\n            if name not in ("Min", "Max") or child.text is None:\n                continue\n            try:\n                values[name.lower()] = float(child.text)\n            except ValueError:\n                pass\n        return values if values else None\n\n    def get_ptz_configuration_options(self, profile: str | None = None) -> dict[str, Any]:\n        """Return the ONVIF PTZ spaces/ranges advertised for the selected profile."""\n        ptz_url, profiles = self._discover_onvif()\n        profile_token = profile or profiles[0]\n        config_token = self._onvif_profile_configs.get(profile_token)\n        if not config_token:\n            raise RuntimeError(f"ONVIF profile {profile_token} has no PTZConfiguration token")\n        body = f\'\'\'<tptz:GetConfigurationOptions xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">\n<tptz:ConfigurationToken>{config_token}</tptz:ConfigurationToken>\n</tptz:GetConfigurationOptions>\'\'\'\n        response = self._soap(\n            ptz_url,\n            body,\n            "http://www.onvif.org/ver20/ptz/wsdl/GetConfigurationOptions",\n        )\n        root = ET.fromstring(response.content)\n        result: dict[str, Any] = {\n            "profile": profile_token,\n            "configuration_token": config_token,\n            "spaces": {},\n        }\n        spaces = next((e for e in root.iter() if _localname(e.tag) == "Spaces"), None)\n        if spaces is not None:\n            for space in spaces:\n                name = _localname(space.tag)\n                item: dict[str, Any] = {}\n                for child in space:\n                    child_name = _localname(child.tag)\n                    if child_name == "URI" and child.text:\n                        item["uri"] = child.text.strip()\n                    elif child_name in ("XRange", "YRange"):\n                        values = self._range_values(child)\n                        if values:\n                            item[child_name[0].lower()] = values\n                result["spaces"].setdefault(name, []).append(item)\n        timeout = next((e for e in root.iter() if _localname(e.tag) == "PTZTimeout"), None)\n        if timeout is not None:\n            timeout_values = {}\n            for child in timeout:\n                if child.text:\n                    timeout_values[_localname(child.tag).lower()] = child.text.strip()\n            if timeout_values:\n                result["timeout"] = timeout_values\n        return result\n\n    def get_zoom(self, profile: str | None = None) -> float:\n''',
)

# ---- ha_mqtt.py: merged control state, D2 zoom command/discovery, keep ptz_enabled metadata
p = ROOT / "ha_mqtt.py"
replace_once(
    p,
    '        self._published_config_count = 0\n',
    '        self._published_config_count = 0\n        self._camera_controls: dict[int, dict[str, Any]] = {}\n',
)
replace_once(
    p,
    '''                elif action == "ptz":\n                    try:\n                        value = json.loads(payload)\n                        if not isinstance(value, dict):\n                            raise ValueError("PTZ payload must be a JSON object")\n                    except (ValueError, json.JSONDecodeError) as exc:\n                        logging.warning("Ignoring invalid D%d PTZ payload %r: %s", source_id, payload, exc)\n                    else:\n                        self.commands.put({"action": "camera_ptz", "source_id": source_id, **value})\n''',
    '''                elif action == "ptz":\n                    try:\n                        value = json.loads(payload)\n                        if not isinstance(value, dict):\n                            raise ValueError("PTZ payload must be a JSON object")\n                    except (ValueError, json.JSONDecodeError) as exc:\n                        logging.warning("Ignoring invalid D%d PTZ payload %r: %s", source_id, payload, exc)\n                    else:\n                        self.commands.put({"action": "camera_ptz", "source_id": source_id, **value})\n                elif action == "zoom" and len(parts) >= 4 and parts[3] == "set":\n                    try:\n                        value = float(payload)\n                    except ValueError:\n                        logging.warning("Ignoring invalid D%d zoom percentage MQTT payload %r", source_id, payload)\n                    else:\n                        self.commands.put({"action": "camera_zoom_set", "source_id": source_id, "percent": value})\n''',
)
replace_once(
    p,
    '''            result.append({\n                "source_id": source_id,\n                "name": str(item.get("name") or f"Camera D{source_id}"),\n                "model": str(item.get("model") or "Uniview camera"),\n            })\n''',
    '''            result.append({\n                "source_id": source_id,\n                "name": str(item.get("name") or f"Camera D{source_id}"),\n                "model": str(item.get("model") or "Uniview camera"),\n                "ptz_enabled": bool(item.get("ptz_enabled", False)),\n            })\n''',
)
replace_once(
    p,
    '''            if bool(camera.get("ptz_enabled", False)):\n                self._camera_config(camera, "switch", "auto_guard", {\n                    "name": "Auto-guard",\n                    "state_topic": f"{event_base}/controls",\n                    "value_template": "{{ 'ON' if value_json.auto_guard else 'OFF' }}",\n                    "command_topic": f"{self.base}/command/camera/D{source_id}/auto_guard",\n                    "payload_on": "ON",\n                    "payload_off": "OFF",\n                    "state_on": "ON",\n                    "state_off": "OFF",\n                    "icon": "mdi:shield-home-outline",\n                })\n''',
    '''            if bool(camera.get("ptz_enabled", False)):\n                self._camera_config(camera, "switch", "auto_guard", {\n                    "name": "Auto-guard",\n                    "state_topic": f"{event_base}/controls",\n                    "value_template": "{{ 'ON' if value_json.auto_guard else 'OFF' }}",\n                    "command_topic": f"{self.base}/command/camera/D{source_id}/auto_guard",\n                    "payload_on": "ON",\n                    "payload_off": "OFF",\n                    "state_on": "ON",\n                    "state_off": "OFF",\n                    "icon": "mdi:shield-home-outline",\n                })\n                if caps.get("ptz_zoom"):\n                    self._camera_config(camera, "number", "zoom", {\n                        "name": "Zoom",\n                        "state_topic": f"{event_base}/controls",\n                        "value_template": "{{ value_json.zoom_percent if value_json.zoom_percent is not none else none }}",\n                        "command_topic": f"{self.base}/command/camera/D{source_id}/zoom/set",\n                        "min": 0,\n                        "max": 100,\n                        "step": 1,\n                        "mode": "slider",\n                        "unit_of_measurement": "%",\n                        "icon": "mdi:magnify",\n                    })\n''',
)
replace_once(
    p,
    '''    def publish_camera_controls(self, source_id: int, state: dict[str, Any]) -> None:\n        if not self.enabled:\n            return\n        self.publish_raw(f"{self.base}/camera/D{int(source_id)}/controls", json.dumps(state, separators=(",", ":")), retain=True)\n''',
    '''    def publish_camera_controls(self, source_id: int, state: dict[str, Any]) -> None:\n        if not self.enabled:\n            return\n        source_id = int(source_id)\n        merged = dict(self._camera_controls.get(source_id, {}))\n        merged.update(state)\n        self._camera_controls[source_id] = merged\n        self.publish_raw(f"{self.base}/camera/D{source_id}/controls", json.dumps(merged, separators=(",", ":")), retain=True)\n''',
)

# ---- app.py: capability probe, D2 zoom state/control and polling
p = ROOT / "app.py"
replace_once(p, '    options["addon_version"] = "1.4.2"\n', '    options["addon_version"] = "1.5.0"\n')
replace_once(p, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.4.2")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.0")\n')
replace_once(
    p,
    '''    camera_clients = build_camera_clients(options)\n    camera_caps, camera_control_channels = probe_camera_controls(camera_clients, camera_definitions(options))\n    options["_camera_capabilities"] = camera_caps\n    mqtt = MQTTDiscovery(options, commands)\n''',
    '''    camera_clients = build_camera_clients(options)\n    camera_defs = camera_definitions(options)\n    camera_caps, camera_control_channels = probe_camera_controls(camera_clients, camera_defs)\n    for camera_def in camera_defs:\n        source_id = int(camera_def.get("source_id", 0))\n        if not bool(camera_def.get("ptz_enabled", False)):\n            continue\n        client = camera_clients.get(source_id)\n        if client is None:\n            continue\n        try:\n            ptz_options = client.get_ptz_configuration_options()\n            camera_caps.setdefault(source_id, {})["ptz_options"] = ptz_options\n            logging.info("D%d ONVIF PTZ configuration options: %s", source_id, json.dumps(ptz_options, sort_keys=True))\n        except Exception as exc:\n            logging.warning("D%d ONVIF PTZ configuration-options probe failed: %s", source_id, exc)\n        try:\n            position = client.get_zoom()\n            camera_caps.setdefault(source_id, {})["ptz_zoom"] = True\n            camera_caps[source_id]["zoom_percent"] = round(position * 100.0, 1)\n            logging.info("D%d ONVIF zoom position: %.6f (%.1f%%)", source_id, position, position * 100.0)\n        except Exception as exc:\n            camera_caps.setdefault(source_id, {})["ptz_zoom"] = False\n            logging.warning("D%d ONVIF zoom-position probe failed: %s", source_id, exc)\n    options["_camera_capabilities"] = camera_caps\n    mqtt = MQTTDiscovery(options, commands)\n''',
)
replace_once(
    p,
    '''        mqtt.publish_camera_controls(source_id, {\n            "day_night_mode": caps.get("day_night_mode"),\n            "illumination": caps.get("illumination_enabled") if caps.get("illumination") else None,\n            "auto_guard": caps.get("auto_guard_enabled"),\n        })\n''',
    '''        mqtt.publish_camera_controls(source_id, {\n            "day_night_mode": caps.get("day_night_mode"),\n            "illumination": caps.get("illumination_enabled") if caps.get("illumination") else None,\n            "auto_guard": caps.get("auto_guard_enabled"),\n            "zoom_percent": caps.get("zoom_percent") if caps.get("ptz_zoom") else None,\n        })\n''',
)
replace_once(
    p,
    '''    ptz_stop_deadlines: dict[int, float] = {}\n    logging.info("Started with %d templates; checking every %d seconds", len(templates["all"]), interval)\n''',
    '''    ptz_stop_deadlines: dict[int, float] = {}\n    ptz_zoom_poll = max(0.2, float(options.get("ptz_zoom_poll_seconds", 1.0)))\n    next_ptz_zoom_poll: dict[int, float] = {\n        source_id: 0.0 for source_id, caps in camera_caps.items() if caps.get("ptz_zoom")\n    }\n    logging.info("Started with %d templates; checking every %d seconds", len(templates["all"]), interval)\n''',
)
replace_once(
    p,
    '''                        elif action == "camera_ptz":\n                            camera_def = next((item for item in camera_definitions(options) if int(item.get("source_id", 0)) == source_id), None)\n''',
    '''                        elif action == "camera_zoom_set":\n                            camera_def = next((item for item in camera_defs if int(item.get("source_id", 0)) == source_id), None)\n                            if not camera_def or not bool(camera_def.get("ptz_enabled", False)) or not camera_caps.get(source_id, {}).get("ptz_zoom"):\n                                logging.warning("D%d does not expose absolute bridge zoom control", source_id)\n                            else:\n                                percent = float(command.get("percent"))\n                                if not 0.0 <= percent <= 100.0:\n                                    raise ValueError(f"D{source_id} zoom percentage must be between 0 and 100, got {percent}")\n                                client.set_zoom(percent / 100.0)\n                                time.sleep(0.15)\n                                position = client.get_zoom()\n                                camera_caps[source_id]["zoom_percent"] = round(position * 100.0, 1)\n                                mqtt.publish_camera_controls(source_id, {"zoom_percent": camera_caps[source_id]["zoom_percent"]})\n                                next_ptz_zoom_poll[source_id] = time.monotonic() + ptz_zoom_poll\n                                logging.info("D%d absolute zoom -> %.1f%% (reported %.1f%%)", source_id, percent, position * 100.0)\n                        elif action == "camera_ptz":\n                            camera_def = next((item for item in camera_defs if int(item.get("source_id", 0)) == source_id), None)\n''',
)
replace_once(
    p,
    '''                if time.monotonic() >= next_check:\n                    status = perform_check(camera, options, templates, state, ha)\n''',
    '''                now_mono = time.monotonic()\n                for source_id, deadline in list(next_ptz_zoom_poll.items()):\n                    if now_mono < deadline:\n                        continue\n                    client = camera_clients.get(source_id)\n                    if client is None:\n                        continue\n                    try:\n                        position = client.get_zoom()\n                        camera_caps[source_id]["zoom_percent"] = round(position * 100.0, 1)\n                        mqtt.publish_camera_controls(source_id, {"zoom_percent": camera_caps[source_id]["zoom_percent"]})\n                    except Exception as exc:\n                        logging.debug("D%d zoom-position poll failed: %s", source_id, exc)\n                    next_ptz_zoom_poll[source_id] = now_mono + ptz_zoom_poll\n\n                if time.monotonic() >= next_check:\n                    status = perform_check(camera, options, templates, state, ha)\n''',
)

# ---- config.yaml version + poll option
p = ROOT / "config.yaml"
replace_once(p, 'version: 1.4.2\n', 'version: 1.5.0\n')
replace_once(p, '  ptz_safety_timeout_seconds: 3.0\n', '  ptz_safety_timeout_seconds: 3.0\n  ptz_zoom_poll_seconds: 1.0\n')
replace_once(p, '  ptz_safety_timeout_seconds: float(0.5,10.0)\n', '  ptz_safety_timeout_seconds: float(0.5,10.0)\n  ptz_zoom_poll_seconds: float(0.2,60.0)\n')

# ---- changelog
p = ROOT / "CHANGELOG.md"
text = p.read_text(encoding="utf-8")
entry = '''## 1.5.0\n\n- Probe and log ONVIF PTZ `GetConfigurationOptions` spaces/ranges for PTZ-enabled cameras.\n- Expose D2/front-PTZ absolute optical zoom as a Home Assistant MQTT `number` slider with actual camera position feedback.\n- Poll PTZ zoom position independently (default 1 s) so wheel/button/preset movements are reflected back into Home Assistant.\n- Merge partial camera-control state publications so zoom/day-night/illumination/auto-guard fields do not erase one another.\n- Preserve `ptz_enabled` in MQTT discovery camera metadata so PTZ-specific entities are discovered reliably.\n\n'''
if not text.startswith("## 1.5.0"):
    p.write_text(entry + text, encoding="utf-8")

print("PTZ capability/front zoom patch applied")
