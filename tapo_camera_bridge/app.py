from __future__ import annotations

import json, logging, math, os, queue, signal, threading, time
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
    safety_client:ONVIFCamera|None=None
    moving:bool=False; moving_pt:bool=False; moving_zoom:bool=False; stop_deadline:float|None=None; stop_deadline_pt:float|None=None; stop_deadline_zoom:float|None=None; next_poll:float=0; last:PTZPosition|None=None
    movement_generation:int=0; stop_in_progress:bool=False; stop_in_progress_pt:bool=False; stop_in_progress_zoom:bool=False; stop_again_generation:int|None=None; stop_again_pt:bool=False; stop_again_zoom:bool=False; stopped_generation_pt:int|None=None; stopped_generation_zoom:int|None=None; commanded_pan:float=0.0; commanded_tilt:float=0.0; state_lock:threading.Lock=field(default_factory=threading.Lock,repr=False)
    stop_condition:threading.Condition=field(init=False,repr=False)
    def __post_init__(self):
        self.stop_condition=threading.Condition(self.state_lock)

class Bridge:
    def __init__(self,opts):
        self.o=opts; self.base=str(opts.get('mqtt_topic','tapo_camera_bridge')).strip('/'); self.dp=str(opts.get('mqtt_discovery_prefix','homeassistant')).strip('/')
        self.q=queue.Queue(); self.pending_command=None; self.cameras={}; self.mqtt=None
        self.watchdog_stop=threading.Event(); self.watchdog_threads={}
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
            r=CameraRuntime(cid,str(raw.get('name') or cid),client,info,caps,presets,safety_client=client.fork()); self.cameras[cid]=r
            logging.info('%s ONVIF PTZ capabilities: %s',r.name,json.dumps(caps,sort_keys=True))
        if not self.cameras:raise RuntimeError('No enabled Tapo cameras configured')
    def mqtt_start(self):
        if not self.o.get('mqtt_enabled',True):return
        c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='tapo_camera_bridge'); user=str(self.o.get('mqtt_username',''))
        if user:c.username_pw_set(user,str(self.o.get('mqtt_password','')))
        c.will_set(f'{self.base}/availability','offline',retain=True); c.on_connect=self.on_connect; c.on_message=self.on_message; self.mqtt=c
        c.connect_async(str(self.o.get('mqtt_host','core-mosquitto')),int(self.o.get('mqtt_port',1883)),60); c.loop_start()
    def sync_moving(self,r):
        r.moving=bool(r.moving_pt or r.moving_zoom)
        r.stop_deadline=min((d for d in (r.stop_deadline_pt if r.moving_pt else None,r.stop_deadline_zoom if r.moving_zoom else None) if d is not None),default=None)

    def target_movement(self,r,send,affects_pt=True,affects_zoom=False):
        # Target moves supersede only axes actually present in their request.
        # A watchdog Stop may legitimately overtake a slow target request on the
        # independent safety session. If that happens, replay the target once
        # after the Stop completes so the final wire order is Stop -> target.
        with r.stop_condition:
            while r.stop_in_progress:r.stop_condition.wait(.1)
            r.movement_generation+=1; generation=r.movement_generation; r.stop_again_generation=None; r.stop_again_pt=False; r.stop_again_zoom=False
            old_pt=r.moving_pt; old_zoom=r.moving_zoom
            now_m=time.monotonic(); transition=float(self.o.get('ptz_transition_safety_seconds',.5))
            if affects_pt and old_pt:r.stop_deadline_pt=now_m+min(max(0.0,(r.stop_deadline_pt or now_m)-now_m),transition)
            if affects_zoom and old_zoom:r.stop_deadline_zoom=now_m+min(max(0.0,(r.stop_deadline_zoom or now_m)-now_m),transition)
            self.sync_moving(r)
        try:
            send()
            with r.stop_condition:
                while r.stop_in_progress:r.stop_condition.wait(.1)
                replay=(r.movement_generation==generation and (
                    (affects_pt and r.stopped_generation_pt==generation) or
                    (affects_zoom and r.stopped_generation_zoom==generation)
                ))
            if replay:send()
        except Exception:
            with r.stop_condition:
                if r.movement_generation==generation and not r.stop_in_progress:
                    now_m=time.monotonic()
                    if affects_pt and old_pt:r.moving_pt=True; r.stop_deadline_pt=now_m
                    if affects_zoom and old_zoom:r.moving_zoom=True; r.stop_deadline_zoom=now_m
                    self.sync_moving(r)
            raise
        else:
            with r.stop_condition:
                if r.movement_generation==generation:
                    if affects_pt:r.moving_pt=False; r.stop_deadline_pt=None; r.commanded_pan=0.0; r.commanded_tilt=0.0
                    if affects_zoom:r.moving_zoom=False; r.stop_deadline_zoom=None
                    self.sync_moving(r)

    def arm_movement(self,r,want_pt=True,want_zoom=False,touch_pt=True,touch_zoom=False,pan_patch=None,tilt_patch=None):
        with r.stop_condition:
            while r.stop_in_progress:r.stop_condition.wait(.1)
            # PanTilt is one ONVIF vector. Resolve partial component patches only
            # after the wait above, so a completed Stop can reset the cached
            # counterpart before this generation is armed.
            pan=max(-1,min(1,float(r.commanded_pan if pan_patch is None else pan_patch)))
            tilt=max(-1,min(1,float(r.commanded_tilt if tilt_patch is None else tilt_patch)))
            if touch_pt and (pan_patch is not None or tilt_patch is not None):want_pt=bool(abs(pan)>1e-6 or abs(tilt)>1e-6)
            old_pt=r.moving_pt; old_zoom=r.moving_zoom
            r.movement_generation+=1; r.stop_again_generation=None; r.stop_again_pt=False; r.stop_again_zoom=False
            now_m=time.monotonic(); timeout=float(self.o.get('ptz_safety_timeout_seconds',3))
            if touch_pt:
                r.moving_pt=bool(want_pt or old_pt); r.stop_deadline_pt=now_m+timeout
            if touch_zoom:
                r.moving_zoom=bool(want_zoom or old_zoom); r.stop_deadline_zoom=now_m+timeout
            self.sync_moving(r)
            return r.movement_generation,old_pt,old_zoom,pan,tilt,want_pt,r.stop_deadline_pt,r.stop_deadline_zoom
    def clear_movement(self,r):
        with r.stop_condition:
            r.movement_generation+=1
            r.stop_again_generation=None; r.stop_again_pt=False; r.stop_again_zoom=False
            r.moving_pt=False; r.moving_zoom=False; r.stop_deadline_pt=None; r.stop_deadline_zoom=None; r.commanded_pan=0.0; r.commanded_tilt=0.0; self.sync_moving(r)
    def safety_stop_once(self,r,expected_generation=None,expected_deadline=None,claim_time=None):
        with r.stop_condition:
            if not r.moving or r.stop_in_progress:return False
            if expected_generation is not None and r.movement_generation!=expected_generation:return False
            self.sync_moving(r)
            if expected_deadline is not None and r.stop_deadline!=expected_deadline:return False
            now_m=time.monotonic() if claim_time is None else claim_time
            stop_pt=bool(r.moving_pt and r.stop_deadline_pt is not None and now_m>=r.stop_deadline_pt)
            stop_zoom=bool(r.moving_zoom and r.stop_deadline_zoom is not None and now_m>=r.stop_deadline_zoom)
            if not stop_pt and not stop_zoom:return False
            r.stop_in_progress=True; r.stop_in_progress_pt=stop_pt; r.stop_in_progress_zoom=stop_zoom; generation=r.movement_generation
        try:(r.safety_client or r.client).stop_move(pan_tilt=stop_pt,zoom=stop_zoom)
        except Exception as e:
            retry=max(.1,float(self.o.get('ptz_stop_retry_seconds',.5)))
            with r.stop_condition:
                if r.movement_generation==generation:
                    retry_at=time.monotonic()+retry
                    if stop_pt and r.moving_pt:r.stop_deadline_pt=retry_at
                    if stop_zoom and r.moving_zoom:r.stop_deadline_zoom=retry_at
                    self.sync_moving(r)
                r.stop_in_progress=False; r.stop_in_progress_pt=False; r.stop_in_progress_zoom=False; r.stop_condition.notify_all()
            logging.exception('%s PTZ safety stop failed; retrying in %.1f s',r.name,retry); self.publish_state(r,False,str(e)); return False
        else:
            with r.stop_condition:
                stop_again=(r.stop_again_generation==generation)
                again_pt=bool(stop_again and r.stop_again_pt)
                again_zoom=bool(stop_again and r.stop_again_zoom)
                if stop_again:
                    r.stop_again_generation=None; r.stop_again_pt=False; r.stop_again_zoom=False
                if r.movement_generation==generation:
                    now_m=time.monotonic()
                    if stop_pt:
                        if again_pt:r.moving_pt=True; r.stop_deadline_pt=now_m
                        else:r.moving_pt=False; r.stop_deadline_pt=None; r.stopped_generation_pt=generation; r.commanded_pan=0.0; r.commanded_tilt=0.0
                    if stop_zoom:
                        if again_zoom:r.moving_zoom=True; r.stop_deadline_zoom=now_m
                        else:r.moving_zoom=False; r.stop_deadline_zoom=None; r.stopped_generation_zoom=generation
                    self.sync_moving(r)
                r.stop_in_progress=False; r.stop_in_progress_pt=False; r.stop_in_progress_zoom=False; r.stop_condition.notify_all()
            return True
    def watchdog_once(self,r,now_mono=None):
        t=time.monotonic() if now_mono is None else now_mono
        with r.stop_condition:
            self.sync_moving(r)
            if not (r.moving and r.stop_deadline is not None and t>=r.stop_deadline and not r.stop_in_progress):return False
            generation=r.movement_generation; deadline=r.stop_deadline
        return self.safety_stop_once(r,generation,deadline,t)
    def watchdog_loop(self,r):
        interval=max(.02,min(.1,float(self.o.get('ptz_watchdog_interval_seconds',.05))))
        while not self.watchdog_stop.wait(interval):self.watchdog_once(r)
    def start_watchdog(self):
        self.watchdog_stop.clear(); self.watchdog_threads={}
        for cid,r in self.cameras.items():
            t=threading.Thread(target=self.watchdog_loop,args=(r,),name=f'ptz-safety-{cid}',daemon=True)
            self.watchdog_threads[cid]=t; t.start()
    def stop_watchdog(self):
        self.watchdog_stop.set()
        current=threading.current_thread()
        for t in list(self.watchdog_threads.values()):
            if t is not current:t.join(timeout=.25)
        self.watchdog_threads={}


    def shutdown_stop_one(self,r):
        with r.stop_condition:
            moving=r.moving
            generation=r.movement_generation
            stop_pt=r.moving_pt; stop_zoom=r.moving_zoom
        if not moving:return
        try:
            # Shutdown is best-effort and generation-aware: never let its late
            # completion clear a newer movement generation.
            (r.safety_client or r.client).stop_move(pan_tilt=stop_pt,zoom=stop_zoom)
        except Exception:
            logging.exception('%s PTZ stop failed during bridge shutdown',r.name)
        else:
            with r.stop_condition:
                if r.movement_generation==generation:
                    if stop_pt:r.moving_pt=False; r.stop_deadline_pt=None; r.commanded_pan=0.0; r.commanded_tilt=0.0
                    if stop_zoom:r.moving_zoom=False; r.stop_deadline_zoom=None
                    self.sync_moving(r)
                    if not r.moving:r.stop_deadline=None
                    r.stop_again_generation=None; r.stop_again_pt=False; r.stop_again_zoom=False
    def shutdown_stops(self):
        # Start every active camera's best-effort Stop before waiting for any
        # one HTTP request. A slow/unreachable camera therefore cannot prevent
        # the other cameras from receiving their shutdown Stop.
        workers=[]
        for r in self.cameras.values():
            with r.stop_condition:moving=r.moving
            if moving:
                t=threading.Thread(target=self.shutdown_stop_one,args=(r,),name=f'ptz-shutdown-{r.camera_id}',daemon=True)
                workers.append(t); t.start()
        wait=max(.1,float(self.o.get('shutdown_stop_wait_seconds',.5)))
        deadline=time.monotonic()+wait
        for t in workers:t.join(max(0,deadline-time.monotonic()))

    def stop_active_axes(self,r,stop_pt=False,stop_zoom=False):
        with r.stop_condition:
            while r.stop_in_progress:r.stop_condition.wait(.1)
            stop_pt=bool(stop_pt and r.moving_pt); stop_zoom=bool(stop_zoom and r.moving_zoom)
            if not stop_pt and not stop_zoom:return
            r.movement_generation+=1; generation=r.movement_generation; r.stop_again_generation=None; r.stop_again_pt=False; r.stop_again_zoom=False
        try:
            r.client.stop_move(pan_tilt=stop_pt,zoom=stop_zoom)
        except Exception:
            with r.stop_condition:
                if r.movement_generation==generation:
                    now_m=time.monotonic()
                    if stop_pt and r.moving_pt:r.stop_deadline_pt=now_m
                    if stop_zoom and r.moving_zoom:r.stop_deadline_zoom=now_m
                    self.sync_moving(r)
            raise
        else:
            with r.stop_condition:
                if r.movement_generation==generation:
                    if stop_pt:r.moving_pt=False; r.stop_deadline_pt=None; r.commanded_pan=0.0; r.commanded_tilt=0.0
                    if stop_zoom:r.moving_zoom=False; r.stop_deadline_zoom=None
                    self.sync_moving(r)

    def execute(self,r,action,d):
        if action=='ptz':
            if d.get('stop'):
                with r.stop_condition:
                    while r.stop_in_progress:
                        r.stop_condition.wait(.1)
                    r.movement_generation+=1
                    generation=r.movement_generation
                    r.stop_again_generation=None; r.stop_again_pt=False; r.stop_again_zoom=False
                    was_moving=r.moving
                    stop_pt=r.moving_pt; stop_zoom=r.moving_zoom
                    if was_moving:
                        deadline=time.monotonic()+float(self.o.get('ptz_transition_safety_seconds',.5))
                        if stop_pt:r.stop_deadline_pt=deadline
                        if stop_zoom:r.stop_deadline_zoom=deadline
                        self.sync_moving(r)
                try:r.client.stop_move(pan_tilt=stop_pt or r.caps.get('pan_tilt_continuous',False),zoom=stop_zoom or r.caps.get('zoom_continuous',False))
                except Exception:
                    if was_moving:
                        with r.stop_condition:
                            if r.movement_generation==generation and not r.stop_in_progress:
                                self.sync_moving(r); r.stop_deadline_pt=time.monotonic() if r.moving_pt else None; r.stop_deadline_zoom=time.monotonic() if r.moving_zoom else None; self.sync_moving(r)
                    raise
                with r.stop_condition:
                    if r.movement_generation==generation:
                        r.moving_pt=False; r.moving_zoom=False; r.stop_deadline_pt=None; r.stop_deadline_zoom=None; r.commanded_pan=0.0; r.commanded_tilt=0.0; self.sync_moving(r)
                return
            supplied_pt=('pan' in d or 'tilt' in d); supplied_zoom=('zoom' in d)
            pt_supported=bool(r.caps.get('pan_tilt_continuous')); zoom_supported=bool(r.caps.get('zoom_continuous'))
            # Capability normalization happens before state mutation and SOAP
            # serialization. Unsupported explicit zeroes are harmless UI noise;
            # unsupported non-zero velocity requests remain errors.
            raw_pan=float(d.get('pan',0)); raw_tilt=float(d.get('tilt',0)); raw_zoom=float(d.get('zoom',0))
            supplied_values=[v for present,v in (('pan' in d,raw_pan),('tilt' in d,raw_tilt),('zoom' in d,raw_zoom)) if present]
            if not all(math.isfinite(v) for v in supplied_values):raise ValueError('PTZ velocities must be finite')
            if supplied_pt and not pt_supported and (abs(raw_pan)>=1e-6 or abs(raw_tilt)>=1e-6):raise RuntimeError('continuous pan/tilt unsupported')
            if supplied_zoom and not zoom_supported and abs(raw_zoom)>=1e-6:raise RuntimeError('continuous zoom unsupported')
            touch_pt=bool(supplied_pt and pt_supported); touch_zoom=bool(supplied_zoom and zoom_supported)
            if not touch_pt and not touch_zoom:
                # A payload containing only explicit zeroes for unsupported axes
                # has no camera-side meaning and must not become a broad Stop.
                if supplied_pt or supplied_zoom:return
                return self.execute(r,'ptz',{'stop':True})
            zoom=max(-1,min(1,raw_zoom))
            want_z=touch_zoom and abs(zoom)>1e-6
            # Wait for any in-flight Stop, resolve the partial PT vector, and
            # arm one generation atomically. This prevents a pre-Stop cached
            # counterpart from being resurrected after the Stop completes.
            generation,old_pt,old_zoom,pan,tilt,want_pt,armed_deadline_pt,armed_deadline_zoom=self.arm_movement(
                r,want_zoom=want_z,touch_pt=touch_pt,touch_zoom=touch_zoom,
                pan_patch=raw_pan if touch_pt and 'pan' in d else None,
                tilt_patch=raw_tilt if touch_pt and 'tilt' in d else None,
            )
            succeeded=False
            try:
                r.client.continuous_move(pan=pan if touch_pt else None,tilt=tilt if touch_pt else None,zoom=zoom if touch_zoom else None)
                succeeded=True
            finally:
                with r.stop_condition:
                    if r.movement_generation==generation:
                        stopped_pt=(r.stopped_generation_pt==generation)
                        stopped_zoom=(r.stopped_generation_zoom==generation)
                        if succeeded:
                            now_m=time.monotonic(); timeout=float(self.o.get('ptz_safety_timeout_seconds',3))
                            overlap_pt=bool(touch_pt and r.stop_in_progress_pt)
                            overlap_zoom=bool(touch_zoom and r.stop_in_progress_zoom)
                            if touch_pt and not overlap_pt:
                                r.moving_pt=bool(want_pt)
                                r.commanded_pan=pan; r.commanded_tilt=tilt
                                # Re-arm only if this generation was actually
                                # stopped while the HTTP request was pending.
                                r.stop_deadline_pt=(now_m+timeout if want_pt and stopped_pt else armed_deadline_pt if want_pt else None)
                            if touch_zoom and not overlap_zoom:
                                r.moving_zoom=bool(want_z)
                                r.stop_deadline_zoom=(now_m+timeout if want_z and stopped_zoom else armed_deadline_zoom if want_z else None)
                            self.sync_moving(r)
                            if overlap_pt or overlap_zoom:
                                r.stop_again_generation=generation; r.stop_again_pt=overlap_pt; r.stop_again_zoom=overlap_zoom
                        elif not r.moving:
                            now_m=time.monotonic()
                            if touch_pt:r.moving_pt=bool(want_pt or old_pt); r.stop_deadline_pt=now_m if r.moving_pt else None
                            if touch_zoom:r.moving_zoom=bool(want_z or old_zoom); r.stop_deadline_zoom=now_m if r.moving_zoom else None
                            self.sync_moving(r)
        elif action=='absolute':
            if not r.caps.get('pan_tilt_absolute'):raise RuntimeError('absolute pan/tilt unsupported')
            pan=float(d['pan']); tilt=float(d['tilt'])
            supplied_zoom=float(d['zoom']) if 'zoom' in d else None
            speed=float(d['speed']) if 'speed' in d else None
            if not all(math.isfinite(v) for v in (pan,tilt) + (() if supplied_zoom is None else (supplied_zoom,)) + (() if speed is None else (speed,))):raise ValueError('absolute PTZ targets must be finite')
            zoom=supplied_zoom if supplied_zoom is not None and r.caps.get('zoom_absolute') else None
            self.target_movement(r,lambda:r.client.absolute_move(pan=pan,tilt=tilt,zoom=zoom,speed=speed),affects_pt=True,affects_zoom=zoom is not None)
        elif action=='relative':
            if not r.caps.get('pan_tilt_relative'):raise RuntimeError('relative pan/tilt unsupported')
            pan=float(d.get('pan',0)); tilt=float(d.get('tilt',0))
            supplied_zoom=float(d['zoom']) if 'zoom' in d else None
            speed=float(d['speed']) if 'speed' in d else None
            if not all(math.isfinite(v) for v in (pan,tilt) + (() if supplied_zoom is None else (supplied_zoom,)) + (() if speed is None else (speed,))):raise ValueError('relative PTZ targets must be finite')
            zoom=supplied_zoom if supplied_zoom is not None and r.caps.get('zoom_relative') else None
            self.target_movement(r,lambda:r.client.relative_move(pan=pan,tilt=tilt,zoom=zoom,speed=speed),affects_pt=True,affects_zoom=zoom is not None)
        elif action=='preset':
            token=str(d['token'])
            if not token:raise ValueError('preset token required')
            # Preset metadata does not reliably tell us whether zoom is part of
            # the preset. Resolve any continuous zoom first so an old zoom
            # watchdog cannot interrupt GotoPreset.
            self.stop_active_axes(r,stop_zoom=True)
            self.target_movement(r,lambda:r.client.goto_preset(token),affects_pt=True,affects_zoom=False)
        r.next_poll=0
    def coalesce_ptz(self,first):
        # Consecutive velocity updates are a patch stream: omission means
        # "leave that axis untouched", so merge latest values per axis instead
        # of replacing the whole payload. Stop/target/other-camera is a barrier.
        cid,action,data=first
        merged=dict(data)
        while True:
            try:x=self.q.get_nowait()
            except queue.Empty:break
            if x[0]==cid and x[1]=='ptz' and not x[2].get('stop'):
                for key in ('pan','tilt','zoom'):
                    if key in x[2]:merged[key]=x[2][key]
                continue
            self.pending_command=x
            break
        return cid,action,merged
    def run(self):
        self.setup(); self.mqtt_start(); self.start_watchdog(); idle=max(.2,float(self.o.get('position_poll_seconds',1))); active=max(.1,float(self.o.get('active_position_poll_seconds',.2)))
        try:
            while not stop_requested:
                try:
                    if self.pending_command is not None:
                        cid,action,d=self.pending_command; self.pending_command=None
                    else:
                        cid,action,d=self.q.get(timeout=.02)
                    r=self.cameras[cid]
                except queue.Empty:r=None
                if r:
                    if action=='ptz' and not d.get('stop'):
                        cid,action,d=self.coalesce_ptz((cid,action,d)); r=self.cameras[cid]
                    try:self.execute(r,action,d)
                    except Exception as e:logging.exception('Camera command failed'); self.publish_state(r,False,str(e))
                t=time.monotonic()
                for r in self.cameras.values():
                    if t>=r.next_poll:
                        try:r.last=r.client.get_status(); self.publish_state(r); r.next_poll=t+(active if r.moving else idle)
                        except Exception as e:logging.debug('%s status poll failed: %s',r.name,e); self.publish_state(r,False,str(e)); r.next_poll=t+idle
        finally:
            self.stop_watchdog()
            # ContinuousMove has no camera-side timeout. Dispatch shutdown
            # Stops independently so one unreachable camera cannot starve the rest.
            self.shutdown_stops()
            if self.mqtt:self.pub(f'{self.base}/availability','offline',True); self.mqtt.disconnect(); self.mqtt.loop_stop()

def main():
    opts=json.loads(open('/data/options.json',encoding='utf-8').read()); logging.basicConfig(level=getattr(logging,str(opts.get('log_level','INFO')).upper(),logging.INFO),format='%(asctime)s %(levelname)s: %(message)s'); Bridge(opts).run()
if __name__=='__main__':main()
