from pathlib import Path

# ----- versioning -----
for path in [
    Path('uniview_camera_bridge/app.py'),
    Path('uniview_camera_bridge/config.yaml'),
    Path('uniview_camera_bridge/CHANGELOG.md'),
    Path('uniview_camera_bridge/DOCS.md'),
]:
    s = path.read_text()
    s = s.replace('1.6.2', '1.7.0-alpha')
    path.write_text(s)

# ----- ONVIF native preset capability -----
p = Path('uniview_camera_bridge/uniview.py')
s = p.read_text()
needle = '''    def get_zoom(self, profile: str | None = None) -> float:\n'''
insert = '''    def get_ptz_node_capabilities(self) -> dict[str, Any]:\n        \"\"\"Return generic PTZ node capabilities relevant to bridge augmentation.\"\"\"\n        ptz_url, _profiles = self._discover_onvif()\n        response = self._soap(\n            ptz_url,\n            '<tptz:GetNodes xmlns:tptz=\"http://www.onvif.org/ver20/ptz/wsdl\"/>',\n            \"http://www.onvif.org/ver20/ptz/wsdl/GetNodes\",\n        )\n        root = ET.fromstring(response.content)\n        maximum_presets = 0\n        home_supported = False\n        for element in root.iter():\n            name = _localname(element.tag)\n            if name == \"MaximumNumberOfPresets\" and element.text:\n                try:\n                    maximum_presets = max(maximum_presets, int(element.text.strip()))\n                except ValueError:\n                    pass\n            elif name == \"HomeSupported\" and element.text:\n                home_supported = home_supported or element.text.strip().lower() in (\"true\", \"1\")\n        return {\n            \"maximum_presets\": maximum_presets,\n            \"presets_supported\": maximum_presets > 0,\n            \"home_supported\": home_supported,\n        }\n\n'''
if insert not in s:
    if needle not in s:
        raise SystemExit('uniview insertion point missing')
    s = s.replace(needle, insert + needle, 1)
p.write_text(s)

# ----- app capability discovery + bridge preset command -----
p = Path('uniview_camera_bridge/app.py')
s = p.read_text()
needle = '''        if caps.get("ptz_zoom_absolute"):\n'''
insert = '''        try:\n            node_caps = client.get_ptz_node_capabilities()\n            caps[\"ptz_max_presets\"] = int(node_caps.get(\"maximum_presets\", 0))\n            caps[\"ptz_native_presets\"] = bool(node_caps.get(\"presets_supported\", False))\n            caps[\"ptz_home_supported\"] = bool(node_caps.get(\"home_supported\", False))\n        except Exception as exc:\n            caps[\"ptz_max_presets\"] = 0\n            caps[\"ptz_native_presets\"] = False\n            logging.debug(\"D%d ONVIF PTZ node-capability probe unavailable: %s\", source_id, exc)\n'''
# add once in startup loop after options probe exception, before zoom check
anchor = '''        except Exception as exc:\n            logging.debug("D%d ONVIF PTZ configuration-options probe unavailable: %s", source_id, exc)\n        if caps.get("ptz_zoom_absolute"):\n'''
replacement = '''        except Exception as exc:\n            logging.debug("D%d ONVIF PTZ configuration-options probe unavailable: %s", source_id, exc)\n''' + insert + '''        if caps.get("ptz_zoom_absolute"):\n'''
if 'caps["ptz_native_presets"]' not in s:
    if anchor not in s:
        raise SystemExit('app capability anchor missing')
    s = s.replace(anchor, replacement, 1)

# Bridge-side zoom preset command sits next to camera_zoom_set.
anchor = '''                        elif action == "camera_zoom_set":\n                            caps = camera_caps.get(source_id, {})\n'''
replacement = '''                        elif action == "camera_zoom_preset":\n                            caps = camera_caps.get(source_id, {})\n                            camera_def = next((item for item in camera_defs if int(item.get(\"source_id\", 0)) == source_id), {})\n                            presets = camera_def.get(\"zoom_presets\") or []\n                            requested = str(command.get(\"name\", \"\"))\n                            preset = next((item for item in presets if isinstance(item, dict) and str(item.get(\"name\", \"\")) == requested), None)\n                            if caps.get(\"ptz_native_presets\"):\n                                logging.warning(\"D%d has native ONVIF presets; bridge-side zoom preset ignored\", source_id)\n                            elif not caps.get(\"ptz_zoom\") or not caps.get(\"ptz_zoom_absolute\"):\n                                logging.warning(\"D%d does not expose ONVIF absolute zoom control\", source_id)\n                            elif preset is None:\n                                logging.warning(\"D%d unknown bridge zoom preset %r\", source_id, requested)\n                            else:\n                                position = float(preset.get(\"position\"))\n                                if not 0.0 <= position <= 1.0:\n                                    raise ValueError(f\"D{source_id} bridge zoom preset {requested!r} position must be 0..1\")\n                                percent = position * 100.0\n                                client.set_zoom(position)\n                                ptz_zoom_targets[source_id] = percent\n                                next_ptz_zoom_poll[source_id] = time.monotonic()\n                                logging.info(\"D%d bridge zoom preset %s -> %.1f%%\", source_id, requested, percent)\n                        elif action == "camera_zoom_set":\n                            caps = camera_caps.get(source_id, {})\n'''
if 'elif action == "camera_zoom_preset"' not in s:
    if anchor not in s:
        raise SystemExit('app zoom command anchor missing')
    s = s.replace(anchor, replacement, 1)
p.write_text(s)

# ----- MQTT discovery -----
p = Path('uniview_camera_bridge/ha_mqtt.py')
s = p.read_text()
s = s.replace('''                "ptz_enabled": bool(item.get("ptz_enabled", False)),\n''', '''                "ptz_enabled": bool(item.get("ptz_enabled", False)),\n                "zoom_presets": item.get("zoom_presets") if isinstance(item.get("zoom_presets"), list) else [],\n''', 1)

# accept preset select commands
anchor = '''                elif action == "zoom" and len(parts) >= 4 and parts[3] == "set":\n                    try:\n                        value = float(payload)\n                    except ValueError:\n                        logging.warning("Ignoring invalid D%d zoom percentage MQTT payload %r", source_id, payload)\n                    else:\n                        self.commands.put({"action": "camera_zoom_set", "source_id": source_id, "percent": value})\n'''
replacement = anchor + '''                elif action == "zoom_preset":\n                    self.commands.put({"action": "camera_zoom_preset", "source_id": source_id, "name": payload})\n'''
if '"action": "camera_zoom_preset"' not in s:
    if anchor not in s:
        raise SystemExit('mqtt command anchor missing')
    s = s.replace(anchor, replacement, 1)

# replace the ptz_enabled block so auto-guard stays policy-gated but zoom is capability-driven
old = '''            if bool(camera.get("ptz_enabled", False)):\n                self._camera_config(camera, "switch", "auto_guard", {\n                    "name": "Auto-guard",\n                    "state_topic": f"{event_base}/controls",\n                    "value_template": "{{ 'ON' if value_json.auto_guard else 'OFF' }}",\n                    "command_topic": f"{self.base}/command/camera/D{source_id}/auto_guard",\n                    "payload_on": "ON",\n                    "payload_off": "OFF",\n                    "state_on": "ON",\n                    "state_off": "OFF",\n                    "icon": "mdi:shield-home-outline",\n                })\n                if caps.get("ptz_zoom"):\n                    self._camera_config(camera, "number", "zoom", {\n                        "name": "Zoom",\n                        "state_topic": f"{event_base}/controls",\n                        "value_template": "{{ value_json.zoom_percent if value_json.zoom_percent is not none else none }}",\n                        "command_topic": f"{self.base}/command/camera/D{source_id}/zoom/set",\n                        "min": 0,\n                        "max": 100,\n                        "step": 1,\n                        "mode": "slider",\n                        "unit_of_measurement": "%",\n                        "icon": "mdi:magnify",\n                    })\n'''
new = '''            if bool(camera.get("ptz_enabled", False)):\n                self._camera_config(camera, "switch", "auto_guard", {\n                    "name": "Auto-guard",\n                    "state_topic": f"{event_base}/controls",\n                    "value_template": "{{ 'ON' if value_json.auto_guard else 'OFF' }}",\n                    "command_topic": f"{self.base}/command/camera/D{source_id}/auto_guard",\n                    "payload_on": "ON",\n                    "payload_off": "OFF",\n                    "state_on": "ON",\n                    "state_off": "OFF",\n                    "icon": "mdi:shield-home-outline",\n                })\n            if caps.get("ptz_zoom") and caps.get("ptz_zoom_absolute"):\n                self._camera_config(camera, "number", "zoom", {\n                    "name": "Zoom",\n                    "state_topic": f"{event_base}/controls",\n                    "value_template": "{{ value_json.zoom_percent if value_json.zoom_percent is not none else none }}",\n                    "command_topic": f"{self.base}/command/camera/D{source_id}/zoom/set",\n                    "min": 0,\n                    "max": 100,\n                    "step": 1,\n                    "mode": "slider",\n                    "unit_of_measurement": "%",\n                    "icon": "mdi:magnify",\n                })\n                bridge_presets = [\n                    item for item in camera.get("zoom_presets", [])\n                    if isinstance(item, dict) and str(item.get("name", "")).strip()\n                ]\n                if bridge_presets and not caps.get("ptz_native_presets"):\n                    self._camera_config(camera, "select", "zoom_preset", {\n                        "name": "Zoom preset",\n                        "command_topic": f"{self.base}/command/camera/D{source_id}/zoom_preset",\n                        "options": [str(item["name"]) for item in bridge_presets],\n                        "icon": "mdi:magnify-scan",\n                    })\n'''
if old not in s:
    raise SystemExit('mqtt ptz discovery block missing')
s = s.replace(old, new, 1)
p.write_text(s)

# ----- config -----
p = Path('uniview_camera_bridge/config.yaml')
s = p.read_text()
# current D6 installation keeps its useful software presets, but mechanism is per-camera/generic.
old = '''  - source_id: 6\n    name: Rear Zoom\n    model: IPC2322SB-DZK-I0\n    enabled: true\n'''
new = '''  - source_id: 6\n    name: Rear Zoom\n    model: IPC2322SB-DZK-I0\n    enabled: true\n    zoom_presets:\n    - name: Wide\n      position: 0.0\n    - name: Medium\n      position: 0.5\n    - name: Tele\n      position: 1.0\n'''
if old in s:
    s = s.replace(old, new, 1)
old_schema = '''    ptz_enabled: bool?\n'''
new_schema = '''    ptz_enabled: bool?\n    zoom_presets:\n    - name: str\n      position: float(0.0,1.0)\n'''
if '    zoom_presets:\n    - name: str\n' not in s:
    s = s.replace(old_schema, new_schema, 1)
p.write_text(s)

# ----- changelog + docs -----
p = Path('uniview_camera_bridge/CHANGELOG.md')
s = p.read_text()
needle = '''- Keep the legacy `rear_zoom_*` path only as a non-blocking compatibility path; new control should use `camera/Dn/zoom/set`.\n'''
extra = needle + '''- Discover native ONVIF preset capacity independently; where absolute zoom exists but native presets do not, optionally augment that camera with configured bridge-side `zoom_presets`.\n- Fix MQTT discovery so generic absolute Zoom entities are capability-driven rather than still being hidden behind `ptz_enabled`.\n'''
if 'bridge-side `zoom_presets`' not in s:
    s = s.replace(needle, extra, 1)
p.write_text(s)

p = Path('uniview_camera_bridge/DOCS.md')
s = p.read_text()
needle = '''ONVIF absolute zoom is normalized to `0.0` (fully wide) through `1.0` (fully telephoto). Some cameras quantize intermediate values internally; the reported Home Assistant state is therefore the camera's actual position rather than merely the last requested target.\n'''
extra = needle + '''\nIf a camera advertises absolute zoom but reports no native ONVIF preset capacity, the bridge can augment it with software presets configured on that camera:\n\n```yaml\n- source_id: 6\n  name: Rear Zoom\n  enabled: true\n  zoom_presets:\n    - name: Wide\n      position: 0.0\n    - name: Yard\n      position: 0.30\n    - name: Tele\n      position: 1.0\n```\n\nThese presets are ordinary absolute zoom targets and appear as a **Zoom preset** select on the same camera device. The mechanism is not tied to D6 or to a model number. If ONVIF reports native preset capacity, the bridge does not publish the software-preset select, avoiding duplicate preset systems.\n'''
if 'If a camera advertises absolute zoom but reports no native ONVIF preset capacity' not in s:
    s = s.replace(needle, extra, 1)
p.write_text(s)
