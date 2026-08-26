from __future__ import annotations

import sys, unittest
from collections import deque
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'shared'))
from onvif_camera import MEDIA_NS, PTZ_NS, ONVIFCamera

class R:
    def __init__(self,text,status=200): self.text=text; self.content=text.encode(); self.status_code=status; self.ok=200<=status<400
    def raise_for_status(self):
        if not self.ok: raise requests.HTTPError(str(self.status_code))
class S:
    def __init__(self,responses): self.responses=deque(responses); self.calls=[]
    def post(self,url,data,headers,timeout): self.calls.append((url,data.decode(),headers,timeout)); return self.responses.popleft()

SERVICES='''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><s:Body><tds:GetServicesResponse><tds:Service><tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace><tds:XAddr>http://10.0.0.9:2020/onvif/service</tds:XAddr></tds:Service><tds:Service><tds:Namespace>http://www.onvif.org/ver20/ptz/wsdl</tds:Namespace><tds:XAddr>http://10.0.0.9:2020/onvif/service</tds:XAddr></tds:Service></tds:GetServicesResponse></s:Body></s:Envelope>'''
PROFILES='''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><trt:GetProfilesResponse><trt:Profiles token="profile_1"><tt:PTZConfiguration token="PTZTOKEN"/></trt:Profiles></trt:GetProfilesResponse></s:Body></s:Envelope>'''
OPTIONS='''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><tptz:GetConfigurationOptionsResponse><tptz:PTZConfigurationOptions><tt:Spaces><tt:AbsolutePanTiltPositionSpace><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange></tt:AbsolutePanTiltPositionSpace><tt:ContinuousPanTiltVelocitySpace><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange></tt:ContinuousPanTiltVelocitySpace></tt:Spaces></tptz:PTZConfigurationOptions></tptz:GetConfigurationOptionsResponse></s:Body></s:Envelope>'''
STATUS='''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><tptz:GetStatusResponse><tptz:PTZStatus><tt:Position><tt:PanTilt x="0.116173" y="0.582241"/></tt:Position><tt:MoveStatus><tt:PanTilt>UNKNOWN</tt:PanTilt></tt:MoveStatus><tt:Error>0</tt:Error></tptz:PTZStatus></tptz:GetStatusResponse></s:Body></s:Envelope>'''
OK='<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body/></s:Envelope>'

class Tests(unittest.TestCase):
    def cam(self,rs): s=S(rs); return ONVIFCamera('192.168.90.113:2020','viewer','secret',session=s,action_in_content_type=False),s
    def test_shared_service_endpoint(self):
        c,s=self.cam([R(SERVICES)]); x=c.get_services(); self.assertEqual(x[MEDIA_NS],'http://192.168.90.113:2020/onvif/service'); self.assertEqual(x[PTZ_NS],x[MEDIA_NS])
    def test_c220_status_and_spaces(self):
        c,_=self.cam([R(SERVICES),R(PROFILES),R(OPTIONS),R(STATUS)]); self.assertIn('AbsolutePanTiltPositionSpace',c.get_ptz_configuration_options()['spaces']); p=c.get_status(); self.assertAlmostEqual(p.pan,.116173); self.assertAlmostEqual(p.tilt,.582241); self.assertEqual(p.error,'0')
    def test_stop_omits_unsupported_zoom(self):
        c,s=self.cam([R(SERVICES),R(PROFILES),R(OK)]); c.stop_move(pan_tilt=True,zoom=False); body=s.calls[-1][1]; self.assertIn('<tptz:PanTilt>true</tptz:PanTilt>',body); self.assertNotIn('<tptz:Zoom>true</tptz:Zoom>',body)

if __name__=='__main__':unittest.main()
