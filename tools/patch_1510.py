from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'uniview_camera_bridge'
U = ROOT / 'uniview.py'
A = ROOT / 'app.py'
C = ROOT / 'config.yaml'
CH = ROOT / 'CHANGELOG.md'


def repl(path, old, new):
    s = path.read_text(encoding='utf-8')
    if old not in s:
        raise RuntimeError(f'expected text not found in {path}')
    path.write_text(s.replace(old, new, 1), encoding='utf-8')

old = '''    def get_exposure(self, channel: int) -> dict[str, Any]:\n        response = self._request("GET", f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure")\n        data = self._lapi_data(response)\n        if not isinstance(data, dict):\n            raise RuntimeError("Exposure settings did not return an object")\n        return data\n'''
new = '''    def get_exposure(self, channel: int) -> dict[str, Any]:\n        response = self._request("GET", f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure")\n        data = self._lapi_data(response)\n        if not isinstance(data, dict):\n            raise RuntimeError("Exposure settings did not return an object")\n        return data\n\n    def get_advanced_exposure(self, channel: int) -> dict[str, Any]:\n        # The native web UI reads /Image/Advanced before writing the private\n        # Exposure endpoint. Values in /Advanced/Exposure are not wire-compatible\n        # with that private write (for example shutter values use a different\n        # representation), so use the same source object as the browser.\n        response = self._request("GET", f"/LAPI/V1.0/Channels/{channel}/Image/Advanced")\n        data = self._lapi_data(response)\n        if not isinstance(data, dict):\n            raise RuntimeError("Advanced image settings did not return an object")\n        exposure = data.get("Exposure")\n        if isinstance(exposure, dict):\n            return exposure\n        # Some firmware variants return the exposure object directly.\n        if "DayNight" in data and "ShutterInfo" in data:\n            return data\n        raise RuntimeError(f"Advanced image settings contain no Exposure object: keys={sorted(data.keys())}")\n'''
repl(U, old, new)

old = '''        # Mirror the camera's current Exposure object and change only the\n        # DayNight mode. This deliberately matches the native web UI's\n        # read/modify/write behaviour instead of synthesising a partial payload.\n        exposure = self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n'''
new = '''        # Mirror the same Exposure representation used by the native web UI.\n        # D2's private write endpoint does not accept the value representation\n        # returned by /Image/Advanced/Exposure, even though the field names look\n        # similar. The browser obtains its write object from /Image/Advanced.\n        exposure = self.get_advanced_exposure(channel) if private else self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n'''
repl(U, old, new)

repl(C, 'version: 1.5.9\n', 'version: 1.5.10\n')
repl(A, '    options["addon_version"] = "1.5.9"\n', '    options["addon_version"] = "1.5.10"\n')
repl(A, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.9")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.10")\n')

text = CH.read_text(encoding='utf-8')
entry = '''## 1.5.10\n\n- Correct D2 Day/Night read-modify-write source: the private exposure write now mirrors the Exposure object from `/Image/Advanced`, matching the native Uniview web UI, instead of incorrectly echoing `/Image/Advanced/Exposure`.\n- Keep the native `text/plain;charset=UTF-8` compact-JSON wire format and change only `DayNight.Mode`.\n\n'''
if '## 1.5.10\n' not in text:
    CH.write_text(entry + text, encoding='utf-8')
