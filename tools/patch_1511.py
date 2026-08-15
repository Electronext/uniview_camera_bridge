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

old = '''    def get_advanced_exposure(self, channel: int) -> dict[str, Any]:\n        # The native web UI reads /Image/Advanced before writing the private\n        # Exposure endpoint. Values in /Advanced/Exposure are not wire-compatible\n        # with that private write (for example shutter values use a different\n        # representation), so use the same source object as the browser.\n        response = self._request("GET", f"/LAPI/V1.0/Channels/{channel}/Image/Advanced")\n        data = self._lapi_data(response)\n        if not isinstance(data, dict):\n            raise RuntimeError("Advanced image settings did not return an object")\n        exposure = data.get("Exposure")\n        if isinstance(exposure, dict):\n            return exposure\n        # Some firmware variants return the exposure object directly.\n        if "DayNight" in data and "ShutterInfo" in data:\n            return data\n        raise RuntimeError(f"Advanced image settings contain no Exposure object: keys={sorted(data.keys())}")\n\n'''
repl(U, old, '')

old = '''        # Mirror the same Exposure representation used by the native web UI.\n        # D2's private write endpoint does not accept the value representation\n        # returned by /Image/Advanced/Exposure, even though the field names look\n        # similar. The browser obtains its write object from /Image/Advanced.\n        exposure = self.get_advanced_exposure(channel) if private else self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n        day_night["Mode"] = values[key]\n        exposure["DayNight"] = day_night\n'''
new = '''        if private:\n            # D2's native web UI writes this complete private-exposure object and\n            # changes only DayNight.Mode. The public /Advanced/Exposure GET uses\n            # a different value representation, while GET /Image/Advanced itself\n            # is unsupported through this forwarded camera interface. Keep this\n            # template byte-for-byte equivalent in structure to the captured UI\n            # request and alter only the requested mode.\n            exposure = {\n                "Mode": 5,\n                "CompensationLevel": 15,\n                "IrisInfo": {"Iris": 100, "MinIris": 0, "MaxIris": 100},\n                "ShutterInfo": {\n                    "Shutter": 9,\n                    "MinShutter": 23,\n                    "MaxShutter": 8,\n                    "IsEnableSlowShutter": 0,\n                    "SlowestShutter": 7,\n                },\n                "GainInfo": {"Gain": 0, "MinGain": 0, "MaxGain": 100},\n                "WideDynamic": {\n                    "Mode": 2,\n                    "Level": 6,\n                    "OpenSensitivity": 9,\n                    "CloseSensitivity": 8,\n                    "SmartSensitivity": 5,\n                },\n                "DayNight": {\n                    "Mode": values[key],\n                    "Sensitivity": 6,\n                    "Time": 3,\n                    "Start": "",\n                    "End": "",\n                },\n                "ExposureCompensationMode": 2,\n                "LinearAntiFlicker": 1,\n                "Metering": {\n                    "Mode": 5,\n                    "RefBrightness": 50,\n                    "HoldTime": 5,\n                    "Area": {\n                        "TopLeft": {"X": 21, "Y": 22},\n                        "BottomRight": {"X": 80, "Y": 65},\n                    },\n                },\n            }\n        else:\n            exposure = self.get_exposure(channel)\n            day_night = dict(exposure.get("DayNight") or {})\n            day_night["Mode"] = values[key]\n            exposure["DayNight"] = day_night\n'''
repl(U, old, new)

repl(C, 'version: 1.5.10\n', 'version: 1.5.11\n')
repl(A, '    options["addon_version"] = "1.5.10"\n', '    options["addon_version"] = "1.5.11"\n')
repl(A, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.10")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.11")\n')

text = CH.read_text(encoding='utf-8')
entry = '''## 1.5.11\n\n- D2 Day/Night now uses the exact complete private Exposure payload observed in a fresh native Uniview web-UI capture, changing only `DayNight.Mode`.\n- Removed the unsupported `GET /Image/Advanced` path introduced in 1.5.10; `/Image/Advanced/Exposure` remains the read source for current HA state only.\n- Preserve the native `text/plain;charset=UTF-8` compact-JSON write format for `/Image/Advanced/Private/Exposure/`.\n\n'''
if '## 1.5.11\n' not in text:
    CH.write_text(entry + text, encoding='utf-8')
