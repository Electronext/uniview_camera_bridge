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
'''        exposure = self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n        day_night["Mode"] = values[key]\n        payload = {"DayNight": day_night} if private else {**exposure, "DayNight": day_night}\n''',
'''        exposure = self.get_exposure(channel)\n        day_night = dict(exposure.get("DayNight") or {})\n        day_night["Mode"] = values[key]\n        if private:\n            # The native D2 web UI sends a fixed five-field DayNight object.\n            # Start/End are present even when switching mode is not scheduled;\n            # omitting them is rejected by the camera with vendor UnSupport.\n            private_day_night = {\n                "Mode": values[key],\n                "Sensitivity": int(day_night.get("Sensitivity", 6)),\n                "Time": int(day_night.get("Time", 3)),\n                "Start": str(day_night.get("Start", "")),\n                "End": str(day_night.get("End", "")),\n            }\n            payload = {"DayNight": private_day_night}\n        else:\n            payload = {**exposure, "DayNight": day_night}\n''')

repl(C, 'version: 1.5.7\n', 'version: 1.5.8\n')
repl(A, '    options["addon_version"] = "1.5.7"\n', '    options["addon_version"] = "1.5.8"\n')
repl(A, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.7")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.8")\n')

text = CH.read_text(encoding='utf-8')
entry = '''## 1.5.8\n\n- Match the native D2 web UI Day/Night payload exactly: `Mode`, `Sensitivity`, `Time`, `Start`, and `End`. The camera rejects the otherwise valid private exposure write with vendor `UnSupport` when the empty `Start`/`End` fields are omitted.\n\n'''
if '## 1.5.8\n' not in text:
    CH.write_text(entry + text, encoding='utf-8')
