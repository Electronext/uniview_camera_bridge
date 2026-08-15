from pathlib import Path
p=Path(__file__).resolve().parents[1]/'uniview_camera_bridge/app.py'
s=p.read_text()
old='''            if event.event_type in state:\n                if event.active is False:\n                    state[event.event_type] = False\n                    self._deadlines.pop((event.source_id, event.event_type), None)\n                elif event.active:\n                    state[event.event_type] = True\n                    self._deadlines[(event.source_id, event.event_type)] = now_mono + self.hold_seconds\n'''
new=old+'''\n            # Compatibility alias for pre-canonicalisation dashboards. New\n            # configurations should use cross_line.\n            if event.event_type == "cross_line":\n                state["line_crossing"] = bool(event.active)\n                if event.active:\n                    self._deadlines[(event.source_id, "line_crossing")] = now_mono + self.hold_seconds\n                else:\n                    self._deadlines.pop((event.source_id, "line_crossing"), None)\n'''
if old not in s: raise SystemExit('state block not found')
p.write_text(s.replace(old,new,1))
