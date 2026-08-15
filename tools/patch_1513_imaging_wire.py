from pathlib import Path

U = Path(__file__).resolve().parents[1] / "uniview_camera_bridge" / "uniview.py"
s = U.read_text(encoding="utf-8")

s = s.replace('''        response = self._soap(\n            self.base_url + "/onvif/imaging",\n            body,\n            "http://www.onvif.org/ver20/imaging/wsdl/GetImagingSettings",\n        )\n''', '''        response = self._imaging_soap(\n            body,\n            "http://www.onvif.org/ver20/imaging/wsdl/GetImagingSettings",\n        )\n''', 1)

s = s.replace('''        self._soap(\n            self.base_url + "/onvif/imaging",\n            body,\n            "http://www.onvif.org/ver20/imaging/wsdl/SetImagingSettings",\n        )\n''', '''        self._imaging_soap(\n            body,\n            "http://www.onvif.org/ver20/imaging/wsdl/SetImagingSettings",\n        )\n''', 1)

anchor = '''    def _normalize_service_url(self, xaddr: str | None) -> str | None:\n'''
method = '''    def _imaging_soap(self, body: str, action: str) -> requests.Response:\n        """Send ONVIF Imaging using the wire format captured from the Uniview NVR."""\n        envelope = f'''<?xml version="1.0" encoding="UTF-8"?>\n<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">\n<s:Header>{self._wsse()}</s:Header>\n<s:Body>{body}</s:Body>\n</s:Envelope>'''\n        url = self.base_url + "/onvif/imaging"\n        logging.debug("ONVIF Imaging request action=%s url=%s body=%s", action, url, body.replace("\\n", " "))\n        response = self.session.post(\n            url,\n            data=envelope.encode("utf-8"),\n            headers={\n                "Content-Type": "application/soap+xml; charset=utf-8",\n                "SOAPAction": f'"{action}"',\n                "User-Agent": "SOAP",\n                "Connection": "close",\n            },\n            timeout=self.timeout,\n        )\n        if not response.ok:\n            logging.error(\n                "ONVIF Imaging SOAP fault action=%s url=%s status=%s body=%s",\n                action, url, response.status_code, response.text[:4000].replace("\\n", " "),\n            )\n        response.raise_for_status()\n        return response\n\n'''
if anchor not in s:
    raise RuntimeError("_normalize_service_url anchor not found")
s = s.replace(anchor, method + anchor, 1)
U.write_text(s, encoding="utf-8")
