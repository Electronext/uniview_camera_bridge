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

repl(U,
'''        # Seed the write body from the ordinary Exposure GET. The packet capture\n        # shows the D2 UI PUTting that exposure structure to Private/Exposure.\n        exposure = self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n        day_night["Mode"] = values[key]\n        exposure["DayNight"] = day_night\n        path = (\n            f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Private/Exposure/"\n            if private else f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure"\n        )\n        response = self._request("PUT", path, json=exposure)\n''',
'''        # Read current DayNight fields from ordinary Exposure, but do not send\n        # the entire generic Exposure object to the vendor Private/Exposure\n        # endpoint. The camera returns vendor UnSupport when unrelated exposure\n        # fields are included. The native UI changes DayNight with this focused\n        # sub-object (Mode plus the existing switching fields).\n        exposure = self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n        day_night["Mode"] = values[key]\n        payload = {"DayNight": day_night} if private else {**exposure, "DayNight": day_night}\n        path = (\n            f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Private/Exposure/"\n            if private else f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure"\n        )\n        logging.debug("Day/night PUT channel=%d private=%s payload=%s", channel, private, payload)\n        response = self._request("PUT", path, json=payload)\n''')

repl(C, 'version: 1.5.6\n', 'version: 1.5.7\n')
repl(A, '    options["addon_version"] = "1.5.6"\n', '    options["addon_version"] = "1.5.7"\n')
repl(A, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.6")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.7")\n')
text = CH.read_text(encoding='utf-8')
entry = '''## 1.5.7\n\n- Send only the `DayNight` sub-object to D2's `Image/Advanced/Private/Exposure/` write endpoint instead of the full generic Exposure payload. The full payload is accepted at HTTP level but rejected by Uniview with `UnSupport`.\n- Log the exact Day/Night PUT payload at DEBUG for packet-level comparison.\n\n'''
if '## 1.5.7\n' not in text:
    CH.write_text(entry + text, encoding='utf-8')
