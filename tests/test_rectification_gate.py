from __future__ import annotations

import importlib.util, sys, tempfile, types, unittest
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'uniview_camera_bridge'))

class FakeMQTT:
    def __init__(self,options,commands):self.options=options; self.commands=commands; self.base='bridge'; self.configs=[]
    def _on_message(self,*args):pass
    def publish_camera_event_discovery(self):pass
    def _camera_definitions(self):return [{'source_id':2,'name':'Front PTZ','model':'x','ptz_enabled':True}]
    def _camera_config(self,*args):self.configs.append(args)

def load_gate():
    tmp=tempfile.TemporaryDirectory(); root=Path(tmp.name); app=types.ModuleType('app'); calls={'perform':0,'execute':0}
    app.STATE_PATH=root/'state.json'; app.STATUS_PATH=root/'status.json'; app.MQTTDiscovery=FakeMQTT
    def atomic(path,payload):
        import json; path.write_text(json.dumps(payload))
    def load(path):
        import json; return json.loads(path.read_text()) if path.exists() else {}
    app.atomic_write_json=atomic; app.load_json=load; app.now=lambda:datetime.fromisoformat('2026-08-26T11:00:00+02:00')
    def perform(*args):calls['perform']+=1; return {'healthy':True,'checked':'x'}
    def execute(*args):calls['execute']+=1; return {'healthy':True,'checked':'x','last_action':args[0].get('action')}
    app.perform_check=perform; app.execute_command=execute; app.main=lambda:0
    hm=types.ModuleType('ha_mqtt'); hm.MQTTDiscovery=FakeMQTT; sys.modules['app']=app; sys.modules['ha_mqtt']=hm
    spec=importlib.util.spec_from_file_location('gate_under_test',ROOT/'uniview_camera_bridge'/'rectification_gate.py'); gate=importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
    return gate,calls,tmp

class Tests(unittest.TestCase):
    def test_disabled_skips_only_scheduled_processing(self):
        gate,calls,tmp=load_gate(); self.addCleanup(tmp.cleanup); state={'rectify_enabled':False}
        result=gate.perform_check(object(),{},object(),state,object()); self.assertEqual(calls['perform'],0); self.assertFalse(result['rectification_enabled']); self.assertIn('heartbeat',result)

    def test_manual_rectify_still_runs_when_auto_disabled(self):
        gate,calls,tmp=load_gate(); self.addCleanup(tmp.cleanup); state={'rectify_enabled':False}
        result=gate.execute_command({'action':'rectify'},object(),{},object(),state,object()); self.assertEqual(calls['execute'],1); self.assertEqual(result['last_action'],'rectify'); self.assertFalse(result['rectification_enabled'])

    def test_manual_run_check_still_runs_when_auto_disabled(self):
        gate,calls,tmp=load_gate(); self.addCleanup(tmp.cleanup); state={'rectify_enabled':False}
        result=gate.execute_command({'action':'run_check'},object(),{},object(),state,object()); self.assertEqual(calls['perform'],1); self.assertEqual(calls['execute'],0); self.assertFalse(result['rectification_enabled'])

    def test_reenable_restores_scheduled_flow(self):
        gate,calls,tmp=load_gate(); self.addCleanup(tmp.cleanup); state={'rectify_enabled':False}; gate.execute_command({'action':'rectify_enabled','enabled':True},object(),{},object(),state,object()); gate.perform_check(object(),{},object(),state,object()); self.assertEqual(calls['perform'],1)

    def test_discovery_name_is_auto_rectification_enabled(self):
        gate,calls,tmp=load_gate(); self.addCleanup(tmp.cleanup); mqtt=gate.RectificationMQTTDiscovery({},object()); mqtt.publish_camera_event_discovery(); self.assertTrue(mqtt.configs); config=mqtt.configs[-1][3]; self.assertEqual(config['name'],'Auto-rectification enabled')

if __name__=='__main__':unittest.main()
