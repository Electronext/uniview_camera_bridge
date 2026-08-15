from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
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
        self._onvif_profile_configs: dict[str, str | None] = {}

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        method = method.upper()
        response: requests.Response | None = None
        for attempt in range(3):
            response = self.session.request(
                method,
                self.base_url + path,
                auth=self.auth,
                timeout=self.timeout,
                **kwargs,
            )
            transient_get_fault = (
                method == "GET"
                and response.status_code == 500
                and "HTTP GET method not implemented" in response.text
            )
            if transient_get_fault and attempt < 2:
                logging.debug(
                    "Transient Uniview GET/Digest fault path=%s attempt=%d/3; resetting HTTP session",
                    path, attempt + 1,
                )
                response.close()
                self.session.close()
                self.session = requests.Session()
                self.auth = HTTPDigestAuth(self.username, self.password)
                time.sleep(0.15 * (attempt + 1))
                continue
            break
        assert response is not None
        if not response.ok:
            logging.error(
                "LAPI HTTP error method=%s path=%s status=%s body=%s",
                method, path, response.status_code, response.text[:4000].replace("\n", " "),
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

    def get_day_night_mode(self, channel: int, private: bool = False) -> str:
        # The native D2 UI writes to the Private/Exposure endpoint, but that
        # endpoint is not a reliable GET endpoint. Read current state from the
        # ordinary Exposure resource and use Private/Exposure only for writes.
        exposure = self.get_exposure(channel)
        day_night = exposure.get("DayNight") or {}
        raw_mode = day_night.get("Mode")
        if raw_mode is None:
            return "Unknown"
        mode = int(raw_mode)
        return {0: "Auto", 1: "Day", 2: "Night"}.get(mode, f"Unknown ({mode})")

    def set_day_night_mode(self, channel: int, mode: str, private: bool = False) -> str:
        values = {"auto": 0, "day": 1, "night": 2}
        key = str(mode).strip().lower()
        if key not in values:
            raise ValueError(f"Unsupported day/night mode: {mode}")
        # Mirror the camera's current Exposure object and change only the
        # DayNight mode. This deliberately matches the native web UI's
        # read/modify/write behaviour instead of synthesising a partial payload.
        exposure = self.get_exposure(channel)
        day_night = dict(exposure.get("DayNight") or {})
        day_night["Mode"] = values[key]
        exposure["DayNight"] = day_night
        path = (
            f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Private/Exposure/"
            if private else f"/LAPI/V1.0/Channels/{channel}/Image/Advanced/Exposure"
        )
        logging.debug("Day/night PUT channel=%d private=%s payload=%s", channel, private, exposure)
        if private:
            # Uniview's own browser UI posts JSON text to this private endpoint
            # as text/plain;charset=UTF-8, not application/json. Preserve that
            # wire format exactly while changing only DayNight.Mode.
            body = json.dumps(exposure, separators=(",", ":"), ensure_ascii=False)
            response = self._request(
                "PUT", path, data=body.encode("utf-8"),
                headers={"Content-Type": "text/plain;charset=UTF-8"},
            )
        else:
            response = self._request("PUT", path, json=exposure)
        self._lapi_data(response)
        return {0: "Auto", 1: "Day", 2: "Night"}[values[key]]

    def get_lamp(self, channel: int) -> dict[str, Any]:
        response = self._request("GET", f"/LAPI/V1.0/Channels/{channel}/Image/LampCtrl")
        data = self._lapi_data(response)
        if not isinstance(data, dict):
            raise RuntimeError("Lamp control did not return an object")
        return data

    def set_lamp(self, channel: int, **updates: Any) -> dict[str, Any]:
        data = self.get_lamp(channel)
        data.update(updates)
        response = self._request("PUT", f"/LAPI/V1.0/Channels/{channel}/Image/LampCtrl", json=data)
        self._lapi_data(response)
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
        self.set_lamp(channel, **{field: 1 if enabled else 0})
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
        profile_info: list[tuple[str, bool, str | None]] = []
        for element in root.iter():
            if _localname(element.tag) != "Profiles" or not element.attrib.get("token"):
                continue
            profile_token = element.attrib["token"]
            ptz_config_token: str | None = None
            for child in element.iter():
                if _localname(child.tag) == "PTZConfiguration":
                    ptz_config_token = child.attrib.get("token")
                    break
            profile_info.append((profile_token, ptz_config_token is not None, ptz_config_token))

        if not profile_info:
            raise RuntimeError("Camera returned no ONVIF media profiles")

        ptz_profiles = [token for token, has_ptz, _config in profile_info if has_ptz]
        fallback_profiles = [token for token, has_ptz, _config in profile_info if not has_ptz]
        profiles = ptz_profiles + fallback_profiles
        logging.debug(
            "ONVIF media profiles: %s",
            "; ".join(
                f"{token} PTZ={'yes' if has_ptz else 'no'}"
                + (f" config={config}" if config else "")
                for token, has_ptz, config in profile_info
            ),
        )
        if ptz_profiles:
            logging.info("Selected ONVIF PTZ profile: %s", ptz_profiles[0])
        else:
            logging.warning(
                "No media profile advertises a PTZConfiguration; falling back to first profile %s",
                profiles[0],
            )

        self._onvif_ptz_url = ptz_url
        self._onvif_profiles = profiles
        self._onvif_profile_configs = {token: config for token, _has_ptz, config in profile_info}
        return ptz_url, profiles

    @staticmethod
    def _range_values(element: ET.Element) -> dict[str, float] | None:
        values: dict[str, float] = {}
        for child in element:
            name = _localname(child.tag)
            if name not in ("Min", "Max") or child.text is None:
                continue
            try:
                values[name.lower()] = float(child.text)
            except ValueError:
                pass
        return values if values else None

    def get_ptz_configuration_options(self, profile: str | None = None) -> dict[str, Any]:
        """Return the ONVIF PTZ spaces/ranges advertised for the selected profile."""
        ptz_url, profiles = self._discover_onvif()
        profile_token = profile or profiles[0]
        config_token = self._onvif_profile_configs.get(profile_token)
        if not config_token:
            raise RuntimeError(f"ONVIF profile {profile_token} has no PTZConfiguration token")
        body = f'''<tptz:GetConfigurationOptions xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
<tptz:ConfigurationToken>{config_token}</tptz:ConfigurationToken>
</tptz:GetConfigurationOptions>'''
        response = self._soap(
            ptz_url,
            body,
            "http://www.onvif.org/ver20/ptz/wsdl/GetConfigurationOptions",
        )
        root = ET.fromstring(response.content)
        result: dict[str, Any] = {
            "profile": profile_token,
            "configuration_token": config_token,
            "spaces": {},
        }
        spaces = next((e for e in root.iter() if _localname(e.tag) == "Spaces"), None)
        if spaces is not None:
            for space in spaces:
                name = _localname(space.tag)
                item: dict[str, Any] = {}
                for child in space:
                    child_name = _localname(child.tag)
                    if child_name == "URI" and child.text:
                        item["uri"] = child.text.strip()
                    elif child_name in ("XRange", "YRange"):
                        values = self._range_values(child)
                        if values:
                            item[child_name[0].lower()] = values
                result["spaces"].setdefault(name, []).append(item)
        timeout = next((e for e in root.iter() if _localname(e.tag) == "PTZTimeout"), None)
        if timeout is not None:
            timeout_values = {}
            for child in timeout:
                if child.text:
                    timeout_values[_localname(child.tag).lower()] = child.text.strip()
            if timeout_values:
                result["timeout"] = timeout_values
        return result

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

