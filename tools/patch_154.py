from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'uniview_camera_bridge'
APP = ROOT / 'app.py'
CONFIG = ROOT / 'config.yaml'
UNIVIEW = ROOT / 'uniview.py'
CHANGELOG = ROOT / 'CHANGELOG.md'


def replace_once(path, old, new):
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise RuntimeError(f'Expected text not found in {path}: {old[:220]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')

# Retry the peculiar transient Uniview HTTP 500/SOAP fault that says HTTP GET
# is not implemented.  The same endpoint succeeds immediately afterwards once
# Digest auth is renegotiated, so reset the session/auth state and retry.
replace_once(UNIVIEW,
'''import logging\nimport os\nfrom datetime import datetime, timezone\n''',
'''import logging\nimport os\nimport time\nfrom datetime import datetime, timezone\n''')

replace_once(UNIVIEW,
'''    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:\n        response = self.session.request(\n            method,\n            self.base_url + path,\n            auth=self.auth,\n            timeout=self.timeout,\n            **kwargs,\n        )\n        if not response.ok:\n            logging.error(\n                "LAPI HTTP error method=%s path=%s status=%s body=%s",\n                method, path, response.status_code, response.text[:4000].replace("\\n", " "),\n            )\n        response.raise_for_status()\n        return response\n''',
'''    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:\n        method = method.upper()\n        response: requests.Response | None = None\n        for attempt in range(3):\n            response = self.session.request(\n                method,\n                self.base_url + path,\n                auth=self.auth,\n                timeout=self.timeout,\n                **kwargs,\n            )\n            transient_get_fault = (\n                method == "GET"\n                and response.status_code == 500\n                and "HTTP GET method not implemented" in response.text\n            )\n            if transient_get_fault and attempt < 2:\n                logging.debug(\n                    "Transient Uniview GET/Digest fault path=%s attempt=%d/3; resetting HTTP session",\n                    path, attempt + 1,\n                )\n                response.close()\n                self.session.close()\n                self.session = requests.Session()\n                self.auth = HTTPDigestAuth(self.username, self.password)\n                time.sleep(0.15 * (attempt + 1))\n                continue\n            break\n        assert response is not None\n        if not response.ok:\n            logging.error(\n                "LAPI HTTP error method=%s path=%s status=%s body=%s",\n                method, path, response.status_code, response.text[:4000].replace("\\n", " "),\n            )\n        response.raise_for_status()\n        return response\n''')

# A configured/manual snapshot channel is authoritative.  Do not silently fall
# back to another lens merely because the requested channel transiently failed.
replace_once(APP,
'''def capture_camera_snapshot(source_id: int, client: UniviewCamera, preferred_channel: int = 1) -> tuple[bytes, int]:\n    last_error: Exception | None = None\n    tried: list[int] = []\n    for channel in (preferred_channel, 0, 1, 2):\n        if channel in tried:\n            continue\n        tried.append(channel)\n        try:\n            image = client.snapshot(channel)\n            if image.startswith(b"\\xff\\xd8"):\n                return image, channel\n            last_error = RuntimeError(f"snapshot channel {channel} did not return JPEG data")\n        except Exception as exc:\n            last_error = exc\n    raise RuntimeError(f"No working snapshot channel for D{source_id}: {last_error}")\n''',
'''def capture_camera_snapshot(source_id: int, client: UniviewCamera, preferred_channel: int = 1) -> tuple[bytes, int]:\n    channel = int(preferred_channel)\n    image = client.snapshot(channel)\n    if not image.startswith(b"\\xff\\xd8"):\n        raise RuntimeError(f"D{source_id} snapshot channel {channel} did not return JPEG data")\n    return image, channel\n''')

replace_once(CONFIG, 'version: 1.5.3\n', 'version: 1.5.4\n')
replace_once(APP, '    options["addon_version"] = "1.5.3"\n', '    options["addon_version"] = "1.5.4"\n')
replace_once(APP, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.3")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.4")\n')

text = CHANGELOG.read_text(encoding='utf-8')
header = '''## 1.5.4\n\n- Retry the camera's transient HTTP 500 `HTTP GET method not implemented` fault by resetting Digest/session state; this was causing D2 image-control commands to abort before their PUT.\n- Treat an explicitly configured manual snapshot channel as authoritative instead of silently falling back to another lens after a transient failure.\n- Keep the packet-capture-proven D2 image/snapshot channel 2 and D3 channel 1 mapping from 1.5.3.\n\n'''
if '## 1.5.4\n' not in text:
    marker = '# Changelog\n\n'
    if marker not in text:
        raise RuntimeError('CHANGELOG header not found')
    CHANGELOG.write_text(text.replace(marker, marker + header, 1), encoding='utf-8')
