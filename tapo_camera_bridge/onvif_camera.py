from __future__ import annotations

import base64, hashlib, os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
import requests

DEVICE_NS='http://www.onvif.org/ver10/device/wsdl'
MEDIA_NS='http://www.onvif.org/ver10/media/wsdl'
PTZ_NS='http://www.onvif.org/ver20/ptz/wsdl'
TT='http://www.onvif.org/ver10/schema'
PAN_POS='http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace'
PAN_REL='http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace'
PAN_VEL='http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace'
PAN_SPEED='http://www.onvif.org/ver10/tptz/PanTiltSpaces/GenericSpeedSpace'
ZOOM_POS='http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace'
ZOOM_REL='http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace'
ZOOM_VEL='http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace'
WSSE_LEGACY='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd#Base64Binary'
WSSE_STANDARD='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary'
WSSE_NONCE_ENCODING_LEGACY=WSSE_LEGACY
WSSE_NONCE_ENCODING_STANDARD=WSSE_STANDARD

def ln(tag:str)->str:return tag.rsplit('}',1)[-1]
def xml_text(value)->str:return escape(str(value))
def xml_attr(value)->str:return escape(str(value), {'"':'&quot;', "'":'&apos;'})


def rng(el):
    out={}
    for c in el:
        if ln(c.tag) in ('Min','Max') and c.text:
            try:out[ln(c.tag).lower()]=float(c.text)
            except ValueError:pass
    return out

@dataclass(frozen=True)
class PTZPosition:
    pan:float|None=None; tilt:float|None=None; zoom:float|None=None
    move_status_pan_tilt:str|None=None; move_status_zoom:str|None=None
    error:str|None=None; utc_time:str|None=None

class ONVIFCamera:
    def __init__(self,host,username,password,timeout=15.0,*,rewrite_xaddr_host=True,action_in_content_type=True,nonce_encoding=WSSE_LEGACY,nonce_bytes=16,prefer_getservices=True,session=None):
        if not host.startswith(('http://','https://')):host='http://'+host
        self.base=host.rstrip('/'); self.username=username; self.password=password
        self.timeout=float(timeout); self.rewrite=bool(rewrite_xaddr_host)
        self.action_in_content_type=bool(action_in_content_type); self.nonce_encoding=nonce_encoding; self.nonce_bytes=int(nonce_bytes); self.prefer_getservices=bool(prefer_getservices)
        self.session=session or requests.Session(); self._services=None; self._profiles=None; self._configs={}; self._device_url=None

    def fork(self):
        """Return an equivalent client with an independent HTTP session.

        Discovery/profile caches are copied so safety/control traffic can run
        concurrently without sharing requests.Session state or rediscovering
        the camera.
        """
        other=ONVIFCamera(self.base,self.username,self.password,self.timeout,
            rewrite_xaddr_host=self.rewrite,action_in_content_type=self.action_in_content_type,
            nonce_encoding=self.nonce_encoding,nonce_bytes=self.nonce_bytes,prefer_getservices=self.prefer_getservices)
        other._services=dict(self._services) if self._services is not None else None
        other._profiles=list(self._profiles) if self._profiles is not None else None
        other._configs=dict(self._configs)
        other._device_url=self._device_url
        return other

    def _wsse(self):
        nonce=os.urandom(self.nonce_bytes); created=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]+'Z'
        digest=hashlib.sha1(nonce+created.encode()+self.password.encode()).digest()
        return f'''<wsse:Security s:mustUnderstand="1" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"><wsse:UsernameToken><wsse:Username>{xml_text(self.username)}</wsse:Username><wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{base64.b64encode(digest).decode()}</wsse:Password><wsse:Nonce EncodingType="{xml_attr(self.nonce_encoding)}">{base64.b64encode(nonce).decode()}</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>'''

    def soap(self,url,body,action):
        env=f'''<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header>{self._wsse()}</s:Header><s:Body>{body}</s:Body></s:Envelope>'''
        ct='application/soap+xml; charset=utf-8'+(f'; action="{action}"' if self.action_in_content_type else '')
        r=self.session.post(url,data=env.encode(),headers={'Content-Type':ct},timeout=self.timeout); r.raise_for_status(); return r

    def _norm(self,url):
        if not url:return None
        p=urlparse(url)
        if self.rewrite and p.path:
            b=urlparse(self.base); q=('?'+p.query) if p.query else ''
            return f'{b.scheme}://{b.netloc}{p.path}{q}'
        return url

    def _device_call(self,body,action):
        # ONVIF commonly uses /onvif/device_service, but some devices (including
        # the tested Tapo C220) expose all services through /onvif/service.
        # Cache the first endpoint that accepts a device-service request.
        urls=[self._device_url] if self._device_url else []
        urls += [self.base+'/onvif/device_service',self.base+'/onvif/service']
        seen=set(); last=None
        for url in urls:
            if not url or url in seen:continue
            seen.add(url)
            try:
                r=self.soap(url,body,action)
                self._device_url=url
                return r
            except requests.HTTPError as e:
                last=e
                response=getattr(e,'response',None)
                status=getattr(response,'status_code',None)
                # Endpoint-shape failures are safe to retry at the alternate
                # standard/common-service path. Authentication and server
                # failures must not be hidden by probing another URL.
                if status not in (400,404,405):raise
        if last:raise last
        raise RuntimeError('No ONVIF device-service endpoint available')

    def services(self):
        if self._services:return dict(self._services)
        out={}
        def getservices():
            r=self._device_call(f'<tds:GetServices xmlns:tds="{DEVICE_NS}"><tds:IncludeCapability>true</tds:IncludeCapability></tds:GetServices>',DEVICE_NS+'/GetServices')
            root=ET.fromstring(r.content); found={}
            for svc in root.iter():
                if ln(svc.tag)!='Service':continue
                ns=x=None
                for c in svc:
                    if ln(c.tag)=='Namespace' and c.text:ns=c.text.strip()
                    elif ln(c.tag)=='XAddr' and c.text:x=c.text.strip()
                if ns and x:found[ns]=self._norm(x)
            return found
        def capabilities():
            r=self._device_call(f'<tds:GetCapabilities xmlns:tds="{DEVICE_NS}"><tds:Category>All</tds:Category></tds:GetCapabilities>',DEVICE_NS+'/GetCapabilities')
            root=ET.fromstring(r.content); found={}
            for p in root.iter():
                ns={'Media':MEDIA_NS,'PTZ':PTZ_NS}.get(ln(p.tag))
                if not ns:continue
                x=next((c.text.strip() for c in p.iter() if ln(c.tag)=='XAddr' and c.text),None)
                if x:found[ns]=self._norm(x)
            return found
        first,second=(getservices,capabilities) if self.prefer_getservices else (capabilities,getservices)
        try:out=first()
        except Exception:out=second()
        out.setdefault(MEDIA_NS,self.base+'/onvif/media'); out.setdefault(PTZ_NS,self.base+'/onvif/ptz')
        self._services=out; return dict(out)

    def device_information(self):
        r=self._device_call(f'<tds:GetDeviceInformation xmlns:tds="{DEVICE_NS}"/>',DEVICE_NS+'/GetDeviceInformation')
        root=ET.fromstring(r.content); out={}
        for e in root.iter():
            key={'Manufacturer':'manufacturer','Model':'model','FirmwareVersion':'firmware_version','HardwareId':'hardware_id','SerialNumber':'serial_number'}.get(ln(e.tag))
            if key:out[key]=e.text.strip() if e.text else None
        return out

    def _discover_profiles(self):
        if self._profiles:return
        r=self.soap(self.services()[MEDIA_NS],f'<trt:GetProfiles xmlns:trt="{MEDIA_NS}"/>',MEDIA_NS+'/GetProfiles'); root=ET.fromstring(r.content)
        rich=[]; plain=[]
        for p in root.iter():
            if ln(p.tag)!='Profiles' or not p.attrib.get('token'):continue
            token=p.attrib['token']; cfg=next((e.attrib.get('token') for e in p.iter() if ln(e.tag)=='PTZConfiguration'),None)
            self._configs[token]=cfg; (rich if cfg else plain).append(token)
        self._profiles=rich+plain
        if not self._profiles:raise RuntimeError('Camera returned no ONVIF media profiles')

    def _profile(self,profile=None):self._discover_profiles(); return profile or self._profiles[0]

    def ptz_options(self,profile=None):
        token=self._profile(profile); cfg=self._configs.get(token)
        if not cfg:raise RuntimeError(f'ONVIF profile {token} has no PTZConfiguration token')
        r=self.soap(self.services()[PTZ_NS],f'<tptz:GetConfigurationOptions xmlns:tptz="{PTZ_NS}"><tptz:ConfigurationToken>{xml_text(cfg)}</tptz:ConfigurationToken></tptz:GetConfigurationOptions>',PTZ_NS+'/GetConfigurationOptions')
        root=ET.fromstring(r.content); spaces={}; node=next((e for e in root.iter() if ln(e.tag)=='Spaces'),None)
        if node is not None:
            for s in node:
                item={}
                for c in s:
                    if ln(c.tag)=='URI' and c.text:item['uri']=c.text.strip()
                    elif ln(c.tag) in ('XRange','YRange'):item[ln(c.tag)[0].lower()]=rng(c)
                spaces.setdefault(ln(s.tag),[]).append(item)
        return {'profile':token,'configuration_token':cfg,'spaces':spaces}

    def node_capabilities(self):
        r=self.soap(self.services()[PTZ_NS],f'<tptz:GetNodes xmlns:tptz="{PTZ_NS}"/>',PTZ_NS+'/GetNodes'); root=ET.fromstring(r.content)
        maxp=0; maxt=0; home=False
        for e in root.iter():
            if ln(e.tag)=='MaximumNumberOfPresets' and e.text:
                try:maxp=max(maxp,int(e.text))
                except ValueError:pass
            elif ln(e.tag)=='MaximumNumberOfPresetTours' and e.text:
                try:maxt=max(maxt,int(e.text))
                except ValueError:pass
            elif ln(e.tag)=='HomeSupported' and e.text:home|=e.text.strip().lower() in ('true','1')
        return {'maximum_presets':maxp,'presets_supported':maxp>0,'maximum_preset_tours':maxt,'home_supported':home}

    def status(self,profile=None):
        token=self._profile(profile); r=self.soap(self.services()[PTZ_NS],f'<tptz:GetStatus xmlns:tptz="{PTZ_NS}"><tptz:ProfileToken>{xml_text(token)}</tptz:ProfileToken></tptz:GetStatus>',PTZ_NS+'/GetStatus'); root=ET.fromstring(r.content)
        pan=tilt=zoom=None; move_pt=move_zoom=err=utc=None
        for e in root.iter():
            if ln(e.tag)=='PanTilt' and 'x' in e.attrib and 'y' in e.attrib:
                try:pan=float(e.attrib['x']); tilt=float(e.attrib['y'])
                except ValueError:pass
            elif ln(e.tag)=='Zoom' and 'x' in e.attrib:
                try:zoom=float(e.attrib['x'])
                except ValueError:pass
            elif ln(e.tag)=='MoveStatus':
                for c in e:
                    if ln(c.tag)=='PanTilt' and c.text:move_pt=c.text.strip()
                    elif ln(c.tag)=='Zoom' and c.text:move_zoom=c.text.strip()
            elif ln(e.tag)=='Error' and e.text:err=e.text.strip()
            elif ln(e.tag)=='UtcTime' and e.text:utc=e.text.strip()
        return PTZPosition(pan,tilt,zoom,move_pt,move_zoom,err,utc)

    def get_zoom(self,profile=None):
        z=self.status(profile).zoom
        if z is None:raise RuntimeError('ONVIF GetStatus returned no Zoom position')
        return z

    def absolute_move(self,*,pan=None,tilt=None,zoom=None,speed=None,profile=None):
        token=self._profile(profile); pos=[]; speeds=[]
        if pan is not None or tilt is not None:
            if pan is None or tilt is None:raise ValueError('Absolute pan/tilt requires both values')
            pan=max(-1,min(1,float(pan))); tilt=max(-1,min(1,float(tilt))); pos.append(f'<tt:PanTilt x="{pan:.9f}" y="{tilt:.9f}" space="{PAN_POS}"/>')
            if speed is not None:
                s=max(0,min(1,float(speed))); speeds.append(f'<tt:PanTilt x="{s:.6f}" y="{s:.6f}" space="{PAN_SPEED}"/>')
        if zoom is not None:pos.append(f'<tt:Zoom x="{max(0,min(1,float(zoom))):.9f}" space="{ZOOM_POS}"/>')
        if not pos:raise ValueError('AbsoluteMove requires a target')
        sx=f'<tptz:Speed>{"".join(speeds)}</tptz:Speed>' if speeds else ''
        body=f'<tptz:AbsoluteMove xmlns:tptz="{PTZ_NS}" xmlns:tt="{TT}"><tptz:ProfileToken>{xml_text(token)}</tptz:ProfileToken><tptz:Position>{"".join(pos)}</tptz:Position>{sx}</tptz:AbsoluteMove>'
        self.soap(self.services()[PTZ_NS],body,PTZ_NS+'/AbsoluteMove')

    def set_zoom(self,target,profile=None):self.absolute_move(zoom=target,profile=profile)

    def relative_move(self,*,pan=None,tilt=None,zoom=None,speed=None,profile=None):
        token=self._profile(profile); parts=[]; speeds=[]
        if pan is not None or tilt is not None:
            pan=max(-1,min(1,float(pan or 0))); tilt=max(-1,min(1,float(tilt or 0))); parts.append(f'<tt:PanTilt x="{pan:.9f}" y="{tilt:.9f}" space="{PAN_REL}"/>')
            if speed is not None:
                s=max(0,min(1,float(speed))); speeds.append(f'<tt:PanTilt x="{s:.6f}" y="{s:.6f}" space="{PAN_SPEED}"/>')
        if zoom is not None:parts.append(f'<tt:Zoom x="{max(-1,min(1,float(zoom))):.9f}" space="{ZOOM_REL}"/>')
        if not parts:raise ValueError('RelativeMove requires a translation')
        sx=f'<tptz:Speed>{"".join(speeds)}</tptz:Speed>' if speeds else ''
        body=f'<tptz:RelativeMove xmlns:tptz="{PTZ_NS}" xmlns:tt="{TT}"><tptz:ProfileToken>{xml_text(token)}</tptz:ProfileToken><tptz:Translation>{"".join(parts)}</tptz:Translation>{sx}</tptz:RelativeMove>'
        self.soap(self.services()[PTZ_NS],body,PTZ_NS+'/RelativeMove')

    def continuous_move(self,pan=None,tilt=None,zoom=None,profile=None):
        # None means omitted; numeric zero is an explicit zero velocity and
        # must be serialized so it can replace an already-active component.
        if pan is None and tilt is None and zoom is None:return self.stop_move(profile)
        if pan is None and tilt is not None:pan=0
        if tilt is None and pan is not None:tilt=0
        token=self._profile(profile); v=[]
        if pan is not None or tilt is not None:
            pan=max(-1,min(1,float(pan or 0))); tilt=max(-1,min(1,float(tilt or 0)))
            v.append(f'<tt:PanTilt x="{pan:.6f}" y="{tilt:.6f}" space="{PAN_VEL}"/>')
        if zoom is not None:
            zoom=max(-1,min(1,float(zoom)))
            v.append(f'<tt:Zoom x="{zoom:.6f}" space="{ZOOM_VEL}"/>')
        body=f'<tptz:ContinuousMove xmlns:tptz="{PTZ_NS}" xmlns:tt="{TT}"><tptz:ProfileToken>{xml_text(token)}</tptz:ProfileToken><tptz:Velocity>{"".join(v)}</tptz:Velocity></tptz:ContinuousMove>'
        self.soap(self.services()[PTZ_NS],body,PTZ_NS+'/ContinuousMove')

    def stop_move(self,profile=None,*,pan_tilt=True,zoom=True):
        if not pan_tilt and not zoom:return
        token=self._profile(profile); axes=('<tptz:PanTilt>true</tptz:PanTilt>' if pan_tilt else '')+('<tptz:Zoom>true</tptz:Zoom>' if zoom else '')
        body=f'<tptz:Stop xmlns:tptz="{PTZ_NS}"><tptz:ProfileToken>{xml_text(token)}</tptz:ProfileToken>{axes}</tptz:Stop>'
        self.soap(self.services()[PTZ_NS],body,PTZ_NS+'/Stop')

    def presets(self,profile=None):
        token=self._profile(profile); r=self.soap(self.services()[PTZ_NS],f'<tptz:GetPresets xmlns:tptz="{PTZ_NS}"><tptz:ProfileToken>{xml_text(token)}</tptz:ProfileToken></tptz:GetPresets>',PTZ_NS+'/GetPresets'); root=ET.fromstring(r.content); out=[]
        for p in root.iter():
            if ln(p.tag)=='Preset':out.append({'token':p.attrib.get('token'),'name':next((e.text.strip() for e in p if ln(e.tag)=='Name' and e.text),None)})
        return out

    def goto_preset(self,preset,profile=None):
        token=self._profile(profile); body=f'<tptz:GotoPreset xmlns:tptz="{PTZ_NS}"><tptz:ProfileToken>{xml_text(token)}</tptz:ProfileToken><tptz:PresetToken>{xml_text(preset)}</tptz:PresetToken></tptz:GotoPreset>'
        self.soap(self.services()[PTZ_NS],body,PTZ_NS+'/GotoPreset')

    get_services=services
    get_device_information=device_information
    get_ptz_configuration_options=ptz_options
    get_ptz_node_capabilities=node_capabilities
    get_status=status
    get_presets=presets
