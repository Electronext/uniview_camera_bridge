from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'uniview_camera_bridge'
APP = ROOT / 'app.py'
CONFIG = ROOT / 'config.yaml'
CHANGELOG = ROOT / 'CHANGELOG.md'


def replace_once(path, old, new):
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise RuntimeError(f'Expected text not found in {path}: {old[:180]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')

# D2/D3 are the two image subchannels (0/1) on the dual-lens device.
# PTZ/Guard Channel/2 is a separate Uniview numbering domain.
replace_once(CONFIG,
'''  - source_id: 2\n    name: Front PTZ\n    model: IPC9312LFW-AF28-2X4\n    enabled: true\n    image_control_channel: 2\n    snapshot_channel: 2\n    ptz_enabled: true\n''',
'''  - source_id: 2\n    name: Front PTZ\n    model: IPC9312LFW-AF28-2X4\n    enabled: true\n    image_control_channel: 0\n    snapshot_channel: 0\n    ptz_enabled: true\n''')
replace_once(CONFIG,
'''  - source_id: 3\n    name: Front Static\n    model: IPC9312LFW-AF28-2X4\n    enabled: true\n    image_control_channel: 1\n''',
'''  - source_id: 3\n    name: Front Static\n    model: IPC9312LFW-AF28-2X4\n    enabled: true\n    image_control_channel: 1\n    snapshot_channel: 1\n''')

# Raw LampCtrl watcher. Only changed values are logged, so the native camera UI
# can be used to identify which fields select IR/white/smart illumination modes.
replace_once(CONFIG,
'''  camera_control_poll_seconds: 60\n  event_history_count: 5\n''',
'''  camera_control_poll_seconds: 60\n  lamp_watch_sources:\n  - 2\n  lamp_watch_seconds: 1.0\n  event_history_count: 5\n''')
replace_once(CONFIG,
'''  camera_control_poll_seconds: int(10,3600)\n  event_history_count: int(1,20)\n''',
'''  camera_control_poll_seconds: int(10,3600)\n  lamp_watch_sources:\n  - int(1,255)\n  lamp_watch_seconds: float(0.2,60.0)?\n  event_history_count: int(1,20)\n''')

replace_once(APP,
'''    camera_control_poll = int(options.get("camera_control_poll_seconds", 60))\n    next_camera_control_poll = time.monotonic() + camera_control_poll\n    ptz_stop_deadlines: dict[int, float] = {}\n''',
'''    camera_control_poll = int(options.get("camera_control_poll_seconds", 60))\n    next_camera_control_poll = time.monotonic() + camera_control_poll\n    lamp_watch_sources = {int(v) for v in options.get("lamp_watch_sources", [])}\n    lamp_watch_seconds = max(0.2, float(options.get("lamp_watch_seconds", 1.0)))\n    next_lamp_watch = 0.0\n    last_lamp_watch: dict[int, str] = {}\n    ptz_stop_deadlines: dict[int, float] = {}\n''')

# Lower idle wake-up latency and avoid a synchronous zoom GetStatus while a
# continuous PTZ move is active.
replace_once(APP, '                command = commands.get(timeout=0.1)\n', '                command = commands.get(timeout=0.02)\n')
replace_once(APP,
'''                for source_id, deadline in list(next_ptz_zoom_poll.items()):\n                    if now_mono < deadline:\n                        continue\n''',
'''                for source_id, deadline in list(next_ptz_zoom_poll.items()):\n                    if now_mono < deadline or source_id in ptz_stop_deadlines:\n                        continue\n''')

# PTZ velocity messages are transient state, not work items. If several have
# accumulated, process only the newest one for that camera so a release/stop
# cannot sit behind stale movement updates.
replace_once(APP,
'''                if command:\n                    action = str(command.get("action", ""))\n''',
'''                if command:\n                    if str(command.get("action", "")) == "camera_ptz":\n                        source_id = int(command.get("source_id", 0))\n                        deferred: list[dict[str, Any]] = []\n                        coalesced = 0\n                        for _ in range(commands.qsize()):\n                            try:\n                                candidate = commands.get_nowait()\n                            except queue.Empty:\n                                break\n                            if str(candidate.get("action", "")) == "camera_ptz" and int(candidate.get("source_id", 0)) == source_id:\n                                command = candidate\n                                coalesced += 1\n                            else:\n                                deferred.append(candidate)\n                        for candidate in deferred:\n                            commands.put(candidate)\n                        if coalesced:\n                            logging.debug("D%d coalesced %d stale PTZ command(s)", source_id, coalesced)\n                    action = str(command.get("action", ""))\n''')

# Watch raw D2 LampCtrl changes before the slower normal camera-control poll.
replace_once(APP,
'''                if time.monotonic() >= next_camera_control_poll:\n''',
'''                if lamp_watch_sources and time.monotonic() >= next_lamp_watch:\n                    for source_id in sorted(lamp_watch_sources):\n                        client = camera_clients.get(source_id)\n                        channel = camera_control_channels.get(source_id)\n                        if client is None or channel is None:\n                            continue\n                        try:\n                            lamp = client.get_lamp(channel)\n                            encoded = json.dumps(lamp, sort_keys=True, separators=(",", ":"))\n                            if last_lamp_watch.get(source_id) != encoded:\n                                logging.info("D%d LampCtrl changed channel=%d: %s", source_id, channel, encoded)\n                                last_lamp_watch[source_id] = encoded\n                        except Exception as exc:\n                            logging.debug("D%d LampCtrl watch failed: %s", source_id, exc)\n                    next_lamp_watch = time.monotonic() + lamp_watch_seconds\n\n                if time.monotonic() >= next_camera_control_poll:\n''')

replace_once(CONFIG, 'version: 1.5.1\n', 'version: 1.5.2\n')
replace_once(APP, '    options["addon_version"] = "1.5.1"\n', '    options["addon_version"] = "1.5.2"\n')
replace_once(APP, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.1")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.2")\n')

text = CHANGELOG.read_text(encoding='utf-8')
entry = '''## 1.5.2\n\n- Correct D2/D3 dual-lens image and snapshot mapping: D2 uses image channel 0 and D3 uses image channel 1; PTZ Channel/2 is a separate numbering domain.\n- Add a configurable raw `LampCtrl` change watcher for identifying IR/white/smart illumination fields from native-UI changes.\n- Coalesce queued PTZ velocity updates so stop commands are not delayed behind stale movement, reduce command-loop idle latency, and suspend zoom-position polling during active continuous movement.\n\n'''
if entry not in text:
    CHANGELOG.write_text(entry + text, encoding='utf-8')
