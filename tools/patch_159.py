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

# Add json import for native text/plain JSON body serialisation.
repl(U, 'import hashlib\nimport logging\n', 'import hashlib\nimport json\nimport logging\n')

old = '''        # Read current DayNight fields from ordinary Exposure, but do not send\n        # the entire generic Exposure object to the vendor Private/Exposure\n        # endpoint. The camera returns vendor UnSupport when unrelated exposure\n        # fields are included. The native UI changes DayNight with this focused\n        # sub-object (Mode plus the existing switching fields).\n        exposure = self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n        day_night["Mode"] = values[key]\n        if private:\n            # The native D2 web UI sends a fixed five-field DayNight object.\n            # Start/End are present even when switching mode is not scheduled;\n            # omitting them is rejected by the camera with vendor UnSupport.\n            private_day_night = {\n                "Mode": values[key],\n                "Sensitivity": int(day_night.get("Sensitivity", 6)),\n                "Time": int(day_night.get("Time", 3)),\n                "Start": str(day_night.get("Start", "")),\n                "End": str(day_night.get("End", "")),\n            }\n            payload = {"DayNight": private_day_night}\n        else:\n            payload = {**exposure, "DayNight": day_night}\n        path = (\n            f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Private/Exposure/"\n            if private else f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure"\n        )\n        logging.debug("Day/night PUT channel=%d private=%s payload=%s", channel, private, payload)\n        response = self._request("PUT", path, json=payload)\n'''
new = '''        # Mirror the camera's current Exposure object and change only the\n        # DayNight mode. This deliberately matches the native web UI's\n        # read/modify/write behaviour instead of synthesising a partial payload.\n        exposure = self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n        day_night["Mode"] = values[key]\n        exposure["DayNight"] = day_night\n        path = (\n            f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Private/Exposure/"\n            if private else f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure"\n        )\n        logging.debug("Day/night PUT channel=%d private=%s payload=%s", channel, private, exposure)\n        if private:\n            # Uniview's own browser UI posts JSON text to this private endpoint\n            # as text/plain;charset=UTF-8, not application/json. Preserve that\n            # wire format exactly while changing only DayNight.Mode.\n            body = json.dumps(exposure, separators=(",", ":"), ensure_ascii=False)\n            response = self._request(\n                "PUT", path, data=body.encode("utf-8"),\n                headers={"Content-Type": "text/plain;charset=UTF-8"},\n            )\n        else:\n            response = self._request("PUT", path, json=exposure)\n'''
repl(U, old, new)

repl(C, 'version: 1.5.8\n', 'version: 1.5.9\n')
repl(A, '    options["addon_version"] = "1.5.8"\n', '    options["addon_version"] = "1.5.9"\n')
repl(A, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.8")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.9")\n')

text = CH.read_text(encoding='utf-8')
entry = '''## 1.5.9\n\n- D2 Day/Night now mirrors the complete current Exposure object, changes only `DayNight.Mode`, and sends the full object back to the native private exposure endpoint.\n- Match the native Uniview web UI wire format for that private write: compact JSON carried as `text/plain;charset=UTF-8` rather than `application/json`.\n\n'''
if '## 1.5.9\n' not in text:
    CH.write_text(entry + text, encoding='utf-8')
