from pathlib import Path

root = Path('uniview_camera_bridge')

p = root / 'uniview.py'
s = p.read_text()
s = s.replace('import hashlib\nimport os\n', 'import hashlib\nimport logging\nimport os\n', 1)
old = '''        response = self.session.post(\n            url,\n            data=envelope.encode("utf-8"),\n            headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},\n            timeout=self.timeout,\n        )\n        response.raise_for_status()\n        return response\n'''
new = '''        logging.debug("ONVIF SOAP request action=%s url=%s body=%s", action, url, body.replace("\\n", " "))\n        response = self.session.post(\n            url,\n            data=envelope.encode("utf-8"),\n            headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},\n            timeout=self.timeout,\n        )\n        if not response.ok:\n            logging.error(\n                "ONVIF SOAP fault action=%s url=%s status=%s body=%s",\n                action,\n                url,\n                response.status_code,\n                response.text[:4000].replace("\\n", " "),\n            )\n        response.raise_for_status()\n        return response\n'''
assert old in s
s = s.replace(old, new, 1)
old = '''        body = f\'''<tptz:ContinuousMove xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">\n<tptz:ProfileToken>{token}</tptz:ProfileToken>\n<tptz:Velocity>\n<tt:PanTilt x="{pan:.6f}" y="{tilt:.6f}" space="{PAN_TILT_VELOCITY_SPACE}"/>\n<tt:Zoom x="{zoom:.6f}" space="{ZOOM_VELOCITY_SPACE}"/>\n</tptz:Velocity>\n</tptz:ContinuousMove>\'''\n        self._soap(ptz_url, body, "http://www.onvif.org/ver20/ptz/wsdl/ContinuousMove")\n'''
new = '''        velocity: list[str] = []\n        if abs(pan) >= 1e-6 or abs(tilt) >= 1e-6:\n            velocity.append(\n                f'<tt:PanTilt x="{pan:.6f}" y="{tilt:.6f}" space="{PAN_TILT_VELOCITY_SPACE}"/>'\n            )\n        if abs(zoom) >= 1e-6:\n            velocity.append(\n                f'<tt:Zoom x="{zoom:.6f}" space="{ZOOM_VELOCITY_SPACE}"/>'\n            )\n        body = f\'''<tptz:ContinuousMove xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">\n<tptz:ProfileToken>{token}</tptz:ProfileToken>\n<tptz:Velocity>{"".join(velocity)}</tptz:Velocity>\n</tptz:ContinuousMove>\'''\n        logging.debug(\n            "ONVIF ContinuousMove profile=%s pan=%.3f tilt=%.3f zoom=%.3f",\n            token, pan, tilt, zoom,\n        )\n        self._soap(ptz_url, body, "http://www.onvif.org/ver20/ptz/wsdl/ContinuousMove")\n'''
assert old in s
s = s.replace(old, new, 1)
p.write_text(s)

p = root / 'config.yaml'
s = p.read_text().replace('version: 1.4.0', 'version: 1.4.1', 1)
p.write_text(s)

p = root / 'app.py'
s = p.read_text().replace('options["addon_version"] = "1.4.0"', 'options["addon_version"] = "1.4.1"', 1)
s = s.replace('UNIVIEW CAMERA BRIDGE STARTING - version 1.4.0', 'UNIVIEW CAMERA BRIDGE STARTING - version 1.4.1', 1)
p.write_text(s)

p = root / 'CHANGELOG.md'
s = p.read_text()
entry = '''## 1.4.1\n\n- Fixed proportional ONVIF PTZ requests to omit unused velocity axes.\n- Added detailed ONVIF SOAP fault logging for rejected PTZ commands.\n\n'''
if not s.startswith('## 1.4.1'):
    s = entry + s
p.write_text(s)
