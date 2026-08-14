from pathlib import Path

root = Path('uniview_camera_bridge')

p = root / 'uniview.py'
s = p.read_text()
old = '''        root = ET.fromstring(response.content)\n        profiles = [\n            element.attrib["token"]\n            for element in root.iter()\n            if _localname(element.tag) == "Profiles" and element.attrib.get("token")\n        ]\n        if not profiles:\n            raise RuntimeError("Camera returned no ONVIF media profiles")\n\n        self._onvif_ptz_url = ptz_url\n        self._onvif_profiles = profiles\n        return ptz_url, profiles\n'''
new = '''        root = ET.fromstring(response.content)\n        profile_info: list[tuple[str, bool, str | None]] = []\n        for element in root.iter():\n            if _localname(element.tag) != "Profiles" or not element.attrib.get("token"):\n                continue\n            profile_token = element.attrib["token"]\n            ptz_config_token: str | None = None\n            for child in element.iter():\n                if _localname(child.tag) == "PTZConfiguration":\n                    ptz_config_token = child.attrib.get("token")\n                    break\n            profile_info.append((profile_token, ptz_config_token is not None, ptz_config_token))\n\n        if not profile_info:\n            raise RuntimeError("Camera returned no ONVIF media profiles")\n\n        ptz_profiles = [token for token, has_ptz, _config in profile_info if has_ptz]\n        fallback_profiles = [token for token, has_ptz, _config in profile_info if not has_ptz]\n        profiles = ptz_profiles + fallback_profiles\n        logging.debug(\n            "ONVIF media profiles: %s",\n            "; ".join(\n                f"{token} PTZ={'yes' if has_ptz else 'no'}"\n                + (f" config={config}" if config else "")\n                for token, has_ptz, config in profile_info\n            ),\n        )\n        if ptz_profiles:\n            logging.info("Selected ONVIF PTZ profile: %s", ptz_profiles[0])\n        else:\n            logging.warning(\n                "No media profile advertises a PTZConfiguration; falling back to first profile %s",\n                profiles[0],\n            )\n\n        self._onvif_ptz_url = ptz_url\n        self._onvif_profiles = profiles\n        return ptz_url, profiles\n'''
assert old in s, 'profile discovery block not found'
s = s.replace(old, new, 1)
p.write_text(s)

p = root / 'config.yaml'
s = p.read_text().replace('version: 1.4.1', 'version: 1.4.2', 1)
p.write_text(s)

p = root / 'app.py'
s = p.read_text()
s = s.replace('options["addon_version"] = "1.4.1"', 'options["addon_version"] = "1.4.2"', 1)
s = s.replace('UNIVIEW CAMERA BRIDGE STARTING - version 1.4.1', 'UNIVIEW CAMERA BRIDGE STARTING - version 1.4.2', 1)
p.write_text(s)

p = root / 'CHANGELOG.md'
s = p.read_text()
entry = '''## 1.4.2\n\n- Select an ONVIF media profile that actually advertises a PTZConfiguration before sending PTZ commands.\n- Log discovered ONVIF media profiles and the selected PTZ profile for diagnostics.\n\n'''
assert '## 1.4.2' not in s
p.write_text(entry + s)
