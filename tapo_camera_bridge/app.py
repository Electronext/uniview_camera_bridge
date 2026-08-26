from __future__ import annotations

import json, logging, os, queue, signal, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import paho.mqtt.client as mqtt
from onvif_camera import ONVIFCamera, PTZPosition, WSSE_NONCE_ENCODING_STANDARD

VERSION='0.1.0'; stop_requested=False

def stop(*_):
    global stop_requested; stop_requested=True
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)

def now():return datetime.now().astimezone().isoformat()
def slug(v):return '_'.join(''.join(c.lower() if c.isalnum() else '_' for c in v).split('_'))

@dataclass
class CameraRuntime:
    camera_id:str; name:str; client:ONVIFCamera; info:dict[str,Any]; caps:dict[str,bool]; presets:list[dict[str,Any]]
    moving:bool=False; stop_deadline:float|None=None; next_poll:float=0; last:PTZPosition|None=None

class Bridge:
    def __init__(self,opts):
        self.o=opts; self.base=str(opts.get('mqtt_topic','tapo_camera_bridge')).strip('/'); self.dp=str(opts.get('mqtt_discovery_prefix','homeassistant')).strip('/')
        self.q=queue.Queue(); self.cameras={}; self.mqtt=None
    def device(self,r):
        return {'identifiers':[f'tapo_bridge_{r.camera_id}'],'name':r.name,'manufacturer':r.info.get('manufacturer') or 'TP-Link','model':r.info.get('model') or 'ONVIF camera','sw_version':r.info.get('firmware_version') or VERSION}
    def pub(self,t,p,retain=False):
        if self.mqtt:self.mqtt.publish(t,p,qos=1,retain=retain)
    def discover_one(self,r):
        node=f'tapo_bridge_{r.camera_id}'; state=f'{self.base}/{r.camera_id}/state'; common={'device':self.device(r),'availability_topic':f'{self.base}/availability'}
        def cfg(comp,obj,data):
            payload={'unique_id':f'{node}_{obj}','default_entity_id':f'{comp}.{slug(r.name)}_{obj}',**common,**data}; self.pub(f'{self.dp}/{comp}/{node}/{obj}/config',json.dumps(payload),True)
        if r.caps.get('pan_tilt_absolute'):
            cfg('sensor','pan_position',{'name':'Pan position','state_topic':state,'value_template':'{{ value_json.pan if value_json.pan is not none else none }}','state_class':'measurement'})
            cfg('sensor','tilt_position',{'name':'Tilt position','state_topic':state,'value_template':'{{ value_json.tilt if value_json.tilt is not none else none }}','state_class':'measurement'})
        if r.caps.get('zoom_absolute'):
            cfg('sensor','zoom_position',{'name':'Zoom position','state_topic':state,'value_template':'{{ value_json.zoom if value_json.zoom is not none else none }}','state_class':'measurement'})
        cfg('binary_sensor','connected',{'name':'ONVIF connected','state_topic':state,'value_template':"{{ 'ON' if value_json.healthy else 'OFF' }}",'payload_on':'ON','payload_off':'OFF','device_class':'connectivity','entity_category':'diagnostic'})
        cfg('sensor','last_update',{'name':'Last PTZ update','state_topic':state,'value_template':'{{ value_json.checked }}','device_class':'timestamp','entity_category':'diagnostic'})
        for i,p in enumerate(r.presets,1):
            token=p.get('token'); name=p.get('name') or f'Preset {token or i}'
            if token is not None:cfg('button',f'preset_{slug(str(token))}',{'name':f'PTZ preset: {name}','command_topic':f'{self.base}/command/{r.camera_id}/preset','payload_press':str(token),'icon':'mdi:camera-control'})
    def publish_state(self,r,healthy=True,error=None):
        p=r.last or PTZPosition(); self.pub(f'{self.base}/{r.camera_id}/state',json.dumps({'healthy':healthy,'checked':now(),'pan':p.pan,'tilt':p.tilt,'zoom':p.zoom,'moving':r.moving,'last_error':error},separators=(',',':')),True)
    def on_connect(self,c,*args):
        c.subscribe(f'{self.base}/command/+/+'); self.pub(f'{self.base}/availability','online',True)
        for r in self.cameras.values():self.discover_one(r)
    def on_message(self,_c,_u,m):
        parts=m.topic.removeprefix(f'{self.base}/command/').split('/',1)
        if len(parts)!=2:return
        cid,action=parts; payload=m.payload.decode(errors='replace')
        if cid not in self.cameras:return
        if action in ('ptz','absolute','relative'):
            try:data=json.loads(payload); assert isinstance(data,dict)
            except Exception:logging.warning('Ignoring invalid %s payload %r',action,payload); return
            self.q.put((cid,action,data))
        elif action=='preset':self.q.put((cid,action,{'token':payload.strip()}))
    def setup(self):
        for raw in self.o.get('cameras',[]):
            if not isinstance(raw,dict) or not raw.get('enabled',True):continue
            cid=slug(str(raw.get('id') or raw.get('name') or 'camera')); host=str(raw.get('host','')).strip(); user=str(raw.get('username','')).strip(); password=str(raw.get('password',''))
            if not host or not user or not password:raise RuntimeError(f'{cid}: host/username/password required')
            client=ONVIFCamera(host,user,password,float(self.o.get('request_timeout_seconds',15)),rewrite_xaddr_host=True,action_in_content_type=False,nonce_encoding=WSSE_NONCE_ENCODING_STANDARD)
            info=client.get_device_information(); spaces=(client.get_ptz_configuration_options().get('spaces') or {})
            caps={'pan_tilt_absolute':bool(spaces.get('AbsolutePanTiltPositionSpace')),'pan_tilt_relative':bool(spaces.get('RelativePanTiltTranslationSpace')),'pan_tilt_continuous':bool(spaces.get('ContinuousPanTiltVelocitySpace')),'zoom_absolute':bool(spaces.get('AbsoluteZoomPositionSpace')),'zoom_relative':bool(spaces.get('RelativeZoomTranslationSpace')),'zoom_continuous':bool(spaces.get('ContinuousZoomVelocitySpace'))}
            try:presets=client.get_presets()
            except Exception:presets=[]
            r=CameraRuntime(cid,str(raw.get('name') or cid),client,info,caps,presets); self.cameras[cid]=r
            logging.info('%s ONVIF PTZ capabilities: %s',r.name,json.dumps(caps,sort_keys=True))
        if not self.cameras:raise RuntimeError('No enabled Tapo cameras configured')
    def mqtt_start(self):
        if not self.o.get('mqtt_enabled',True):return
        c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='tapo_camera_bridge'); user=str(self.o.get('mqtt_username',''))
        if user:c.username_pw_set(user,str(self.o.get('mqtt_password','')))
        c.will_set(f'{self.base}/availability','offline',retain=True); c.on_connect=self.on_connect; c.on_message=self.on_message; self.mqtt=c
        c.connect_async(str(self.o.get('mqtt_host','core-mosquitto')),int(self.o.get('mqtt_port',1883)),60); c.loop_start()
    def execute(self,r,action,d):
        if action=='ptz':
            if d.get('stop'):
                r.client.stop_move(pan_tilt=r.caps.get('pan_tilt_continuous',False),zoom=r.caps.get('zoom_continuous',False)); r.moving=False; r.stop_deadline=None; return
            pan=max(-1,min(1,float(d.get('pan',0)))); tilt=max(-1,min(1,float(d.get('tilt',0)))); zoom=max(-1,min(1,float(d.get('zoom',0))))
            want_pt=abs(pan)>1e-6 or abs(tilt)>1e-6; want_z=abs(zoom)>1e-6
            if want_pt and not r.caps.get('pan_tilt_continuous'):raise RuntimeError('continuous pan/tilt unsupported')
            if want_z and not r.caps.get('zoom_continuous'):raise RuntimeError('continuous zoom unsupported')
            if not want_pt and not want_z:
                r.client.stop_move(pan_tilt=r.caps.get('pan_tilt_continuous',False),zoom=r.caps.get('zoom_continuous',False)); r.moving=False; r.stop_deadline=None; return
            r.client.continuous_move(pan=pan,tilt=tilt,zoom=zoom); r.moving=True; r.stop_deadline=time.monotonic()+float(self.o.get('ptz_safety_timeout_seconds',3))
        elif action=='absolute':
            if not r.caps.get('pan_tilt_absolute'):raise RuntimeError('absolute pan/tilt unsupported')
            r.client.absolute_move(pan=float(d['pan']),tilt=float(d['tilt']),zoom=(float(d['zoom']) if 'zoom' in d and r.caps.get('zoom_absolute') else None),speed=(float(d['speed']) if 'speed' in d else None))
        elif action=='relative':
            if not r.caps.get('pan_tilt_relative'):raise RuntimeError('relative pan/tilt unsupported')
            r.client.relative_move(pan=float(d.get('pan',0)),tilt=float(d.get('tilt',0)),zoom=(float(d['zoom']) if 'zoom' in d and r.caps.get('zoom_relative') else None),speed=(float(d['speed']) if 'speed' in d else None))
        elif action=='preset':r.client.goto_preset(d['token'])
        r.next_poll=0
    def run(self):
        self.setup(); self.mqtt_start(); idle=max(.2,float(self.o.get('position_poll_seconds',1))); active=max(.1,float(self.o.get('active_position_poll_seconds',.2)))
        try:
            while not stop_requested:
                try:cid,action,d=self.q.get(timeout=.02); r=self.cameras[cid]
                except queue.Empty:r=None
                if r:
                    if action=='ptz':
                        latest=(cid,action,d); deferred=[]
                        while True:
                            try:x=self.q.get_nowait()
                            except queue.Empty:break
                            if x[0]==cid and x[1]=='ptz':latest=x
                            else:deferred.append(x)
                        for x in deferred:self.q.put(x)
                        cid,action,d=latest; r=self.cameras[cid]
                    try:self.execute(r,action,d)
                    except Exception as e:logging.exception('Camera command failed'); self.publish_state(r,False,str(e))
                t=time.monotonic()
                for r in self.cameras.values():
                    if r.stop_deadline and t>=r.stop_deadline:
                        try:r.client.stop_move(pan_tilt=r.caps.get('pan_tilt_continuous',False),zoom=r.caps.get('zoom_continuous',False))
                        finally:r.moving=False; r.stop_deadline=None
                    if t>=r.next_poll:
                        try:r.last=r.client.get_status(); self.publish_state(r); r.next_poll=t+(active if r.moving else idle)
                        except Exception as e:logging.debug('%s status poll failed: %s',r.name,e); self.publish_state(r,False,str(e)); r.next_poll=t+idle
        finally:
            if self.mqtt:self.pub(f'{self.base}/availability','offline',True); self.mqtt.disconnect(); self.mqtt.loop_stop()

def main():
    opts=json.loads(open('/data/options.json',encoding='utf-8').read()); logging.basicConfig(level=getattr(logging,str(opts.get('log_level','INFO')).upper(),logging.INFO),format='%(asctime)s %(levelname)s: %(message)s'); Bridge(opts).run()
if __name__=='__main__':main()
