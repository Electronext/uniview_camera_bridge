from __future__ import annotations

import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

ZOOM_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace"
PAN_TILT_VELOCITY_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
ZOOM_VELOCITY_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class UniviewCamera:
    """Small client for the Uniview LAPI calls used by this add-on."""

    def __init__(self, host: str, username: str, password: str, timeout: float = 15.0):
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        self.base_url = host.rstrip("/")
        self.username = username
        self.password = password
        self.auth = HTTPDigestAuth(username, password)
        self.timeout = timeout
        self.session = requests.Session()
        self._onvif_ptz_url: str | None = None
        self._onvif_profiles: list[str] | None = None

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        response = self.session.request(
            method,
            self.base_url + path,
            auth=self.auth,
            timeout=self.timeout,
            **kwargs,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _lapi_data(response: requests.Response) -> Any:
        """Return LAPI Response.Data and reject HTTP-200 vendor errors."""
        try:
            body = response.json()
        except ValueError:
            return None
        wrapper = body.get("Response") if isinstance(body, dict) else None
        if not isinstance(wrapper, dict):
            return body
        code = wrapper.get("ResponseCode", 0)
        status = wrapper.get("StatusCode", 0)
        if code not in (0, None) or status not in (0, None):
            text = wrapper.get("StatusString") or wrapper.get("ResponseString") or "LAPI error"
            raise RuntimeError(f"{text} (ResponseCode={code}, StatusCode={status})")
        return wrapper.get("Data")

    def snapshot(self, channel: int) -> bytes:
        return self._request(
            "GET", f"/LAPI/V1.0/Channels/{channel}/Media/Video/Streams/0/Snapshot"
        ).content

    def image_capabilities(self, channel: int) -> dict[str, Any]:
        response = self._request("GET", f"/LAPI/V1.0/Channels/{channel}/Image/Capabilities")
        data = self._lapi_data(response)
        if not isinstance(data, dict):
            raise RuntimeError("Image capabilities did not return an object")
        return data

    def get_exposure(self, channel: int) -> dict[str, Any]:
        response = self._request("GET", f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure")
        data = self._lapi_data(response)
        if not isinstance(data, dict):
            raise RuntimeError("Exposure settings did not return an object")
        return data

    def get_day_night_mode(self, channel: int) -> str:
        exposure = self.get_exposure(channel)
        day_night = exposure.get("DayNight") or {}
        mode = int(day_night.get("Mode"))
        return {0: "Auto", 1: "Day", 2: "Night"}.get(mode, f"Unknown ({mode})")

    def set_day_night_mode(self, channel: int, mode: str) -> str:
        values = {"auto": 0, "day": 1, "night": 2}
        key = str(mode).strip().lower()
        if key not in values:
            raise ValueError(f"Unsupported day/night mode: {mode}")
        exposure = self.get_exposure(channel)
        day_night = dict(exposure.get("DayNight") or {})
        day_night["Mode"] = values[key]
        exposure["DayNight"] = day_night
        response = self._request("PUT", f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure", json=exposure)
        self._lapi_data(response)
        return {0: "Auto", 1: "Day", 2: "Night"}[values[key]]

    def get_lamp(self, channel: int) -> dict[str, Any]:
        response = self._request("GET", f"/LAPI/V1.0/Channels/{channel}/Image/LampCtrl")
        data = self._lapi_data(response)
        if not isinstance(data, dict):
            raise RuntimeError("Lamp control did not return an object")
        return data

    def get_lamp_enabled(self, channel: int) -> bool:
        data = self.get_lamp(channel)
        value = data.get("Enabled", data.get("Enable"))
        if value is None:
            raise RuntimeError("Lamp control response has no Enabled/Enable field")
        return bool(value)

    def set_lamp_enabled(self, channel: int, enabled: bool) -> bool:
        data = self.get_lamp(channel)
        field = "Enabled" if "Enabled" in data else "Enable" if "Enable" in data else "Enabled"
        data[field] = 1 if enabled else 0
        response = self._request("PUT", f"/LAPI/V1.0/Channels/{channel}/Image/LampCtrl", json=data)
        self._lapi_data(response)
        return bool(enabled)

    def get_auto_guard(self) -> bool:
        response = self._request("GET", "/LAPI/V1.0/Channel/2/PTZ/Guard")
        data = self._lapi_data(response)
        if not isinstance(data, dict):
            raise RuntimeError("Auto-guard status did not return an object")
        value = data.get("Enabled", data.get("Enable"))
        if value is None:
            raise RuntimeError("Auto-guard status has no Enabled/Enable field")
        return bool(value)

    def set_auto_guard(self, enabled: bool, preset: int, guard_time: int) -> None:
        payload = {
            "Enabled": 1 if enabled else 0,
            "Mode": 0,
            "Param": preset,
            "Time": guard_time,
        }
        response = self._request("PUT", "/LAPI/V1.0/Channel/2/PTZ/Guard", json=payload)
        self._lapi_data(response)

    def rectify(self) -> None:
        self._request("PUT", "/LAPI/V1.0/Channels/2/PTZ/Rectify")

    def goto_preset(self, preset: int, channel: int = 2) -> None:
        self._request("PUT", f"/LAPI/V1.0/Channels/{channel}/PTZ/Presets/{int(preset)}/goto")

    def get_osd(self, channel: int) -> dict[str, Any]:
        data = self._request("GET", f"/LAPI/V1.0/Channel/{channel}/Media/OSD/0").json()
        return data["Response"]["Data"]

    def set_osd(self, channel: int, data: dict[str, Any]) -> None:
        self._request("PUT", f"/LAPI/V1.0/Channel/{channel}/Media/OSD/0", json=data)

    # ---- ONVIF zoom support -------------------------------------------------

    def _wsse(self) -> str:
        nonce = os.urandom(16)
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        digest = hashlib.sha1(
            nonce + created.encode("utf-8") + self.password.encode("utf-8")
        ).digest()
        return f'''<wsse:Security s:mustUnderstand="1" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
<wsse:UsernameToken>
<wsse:Username>{self.username}</wsse:Username>
<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{base64.b64encode(digest).decode()}</wsse:Password>
<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce>
<wsu:Created>{created}</wsu:Created>
</wsse:UsernameToken>
</wsse:Security>'''

    def _soap(self, url: str, body: str, action: str) -> requests.Response:
        envelope = f'''<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Header>{self._wsse()}</s:Header>
<s:Body>{body}</s:Body>
</s:Envelope>'''
        logging.debug("ONVIF SOAP request action=%s url=%s body=%s", action, url, body.replace("\n", " "))
        response = self.session.post(
            url,
            data=envelope.encode("utf-8"),
            headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},
            timeout=self.timeout,
        )
        if not response.ok:
            logging.error(
                "ONVIF SOAP fault action=%s url=%s status=%s body=%s",
                action,
                url,
                response.status_code,
                response.text[:4000].replace("\n", " "),
            )
        response.raise_for_status()
        return response

    def _normalize_service_url(self, xaddr: str | None) -> str | None:
        if not xaddr:
            return None
        base = urlparse(self.base_url)
        parsed = urlparse(xaddr)
        if parsed.path:
            return f"{base.scheme}://{base.netloc}{parsed.path}"
        return xaddr

    def _discover_onvif(self) -> tuple[str, list[str]]:
        if self._onvif_ptz_url and self._onvif_profiles:
            return self._onvif_ptz_url, self._onvif_profiles

        device_url = self.base_url + "/onvif/device_service"
        body = '<tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Category>All</tds:Category></tds:GetCapabilities>'
        response = self._soap(
            device_url,
            body,
            "http://www.onvif.org/ver10/device/wsdl/GetCapabilities",
        )
        root = ET.fromstring(response.content)
        media_url: str | None = None
        ptz_url: str | None = None
        for element in root.iter():
            if _localname(element.tag) != "XAddr" or not (element.text or "").strip():
                continue
            xaddr = element.text.strip()
            lower = xaddr.lower()
            if "/media" in lower and media_url is None:
                media_url = self._normalize_service_url(xaddr)
            if "/ptz" in lower and ptz_url is None:
                ptz_url = self._normalize_service_url(xaddr)
        media_url = media_url or self.base_url + "/onvif/media"
        ptz_url = ptz_url or self.base_url + "/onvif/ptz"

        response = self._soap(
            media_url,
            '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
            "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
        )
        root = ET.fromstring(response.content)
        profiles = [
            element.attrib["token"]
            for element in root.iter()
            if _localname(element.tag) == "Profiles" and element.attrib.get("token")
        ]
        if not profiles:
            raise RuntimeError("Camera returned no ONVIF media profiles")

        self._onvif_ptz_url = ptz_url
        self._onvif_profiles = profiles
        return ptz_url, profiles

    def get_zoom(self, profile: str | None = None) -> float:
        ptz_url, profiles = self._discover_onvif()
        token = profile or profiles[0]
        body = f'''<tptz:GetStatus xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
<tptz:ProfileToken>{token}</tptz:ProfileToken>
</tptz:GetStatus>'''
        response = self._soap(
            ptz_url,
            body,
            "http://www.onvif.org/ver20/ptz/wsdl/GetStatus",
        )
        root = ET.fromstring(response.content)
        for element in root.iter():
            if _localname(element.tag) == "Zoom" and "x" in element.attrib:
                return float(element.attrib["x"])
        raise RuntimeError("ONVIF GetStatus returned no numeric Zoom x position")

    def set_zoom(self, target: float, profile: str | None = None) -> None:
        target = max(0.0, min(1.0, float(target)))
        ptz_url, profiles = self._discover_onvif()
        token = profile or profiles[0]
        body = f'''<tptz:AbsoluteMove xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<tptz:ProfileToken>{token}</tptz:ProfileToken>
<tptz:Position>
<tt:Zoom x="{target:.9f}" space="{ZOOM_SPACE}"/>
</tptz:Position>
</tptz:AbsoluteMove>'''
        self._soap(
            ptz_url,
            body,
            "http://www.onvif.org/ver20/ptz/wsdl/AbsoluteMove",
        )

    def continuous_move(
        self,
        pan: float = 0.0,
        tilt: float = 0.0,
        zoom: float = 0.0,
        profile: str | None = None,
    ) -> None:
        pan = max(-1.0, min(1.0, float(pan)))
        tilt = max(-1.0, min(1.0, float(tilt)))
        zoom = max(-1.0, min(1.0, float(zoom)))
        if abs(pan) < 1e-6 and abs(tilt) < 1e-6 and abs(zoom) < 1e-6:
            self.stop_move(profile=profile)
            return
        ptz_url, profiles = self._discover_onvif()
        token = profile or profiles[0]
        velocity: list[str] = []
        if abs(pan) >= 1e-6 or abs(tilt) >= 1e-6:
            velocity.append(
                f'<tt:PanTilt x="{pan:.6f}" y="{tilt:.6f}" space="{PAN_TILT_VELOCITY_SPACE}"/>'
            )
        if abs(zoom) >= 1e-6:
            velocity.append(
                f'<tt:Zoom x="{zoom:.6f}" space="{ZOOM_VELOCITY_SPACE}"/>'
            )
        body = f'''<tptz:ContinuousMove xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<tptz:ProfileToken>{token}</tptz:ProfileToken>
<tptz:Velocity>{"".join(velocity)}</tptz:Velocity>
</tptz:ContinuousMove>'''
        logging.debug(
            "ONVIF ContinuousMove profile=%s pan=%.3f tilt=%.3f zoom=%.3f",
            token, pan, tilt, zoom,
        )
        self._soap(ptz_url, body, "http://www.onvif.org/ver20/ptz/wsdl/ContinuousMove")

    def stop_move(self, profile: str | None = None) -> None:
        ptz_url, profiles = self._discover_onvif()
        token = profile or profiles[0]
        body = f'''<tptz:Stop xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
<tptz:ProfileToken>{token}</tptz:ProfileToken>
<tptz:PanTilt>true</tptz:PanTilt>
<tptz:Zoom>true</tptz:Zoom>
</tptz:Stop>'''
        self._soap(ptz_url, body, "http://www.onvif.org/ver20/ptz/wsdl/Stop")

