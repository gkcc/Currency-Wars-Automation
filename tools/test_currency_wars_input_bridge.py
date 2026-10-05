"""No-input regressions for fixed-task routing and uncertain launch handling."""
import json
import subprocess
import unittest
from unittest.mock import patch

import currency_wars_artifacts as artifacts
import currency_wars_input_bridge as bridge

SID = artifacts._windows_user_sid()
CONFIG = {'user_sid': SID, 'task_name': bridge.task_name(SID)}

def definition(**changes):
    value = dict(name=CONFIG['task_name'], user=SID, enabled=True, logon_type=3,
                 run_level=1, multiple_instances=2, task_owner='S-1-5-32-544',
                 executable=str(bridge.INSTALL_ROOT/'python/python.exe'),
                 arguments=subprocess.list2cmdline(['-I','-S','-B','-X','utf8',
                         str(bridge.INSTALL_ROOT/'code/currency_wars_bridge_task.py')]),
                 working_directory=str(bridge.INSTALL_ROOT),
                 security=f'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;{SID})',
                 instances=[],last_result=0)
    return {**value, **changes}

class InputBridgeTests(unittest.TestCase):
    def test_fixed_task_definition_and_read_execute_owner(self):
        self.assertEqual(bridge.validate_task(CONFIG, definition()), definition())
        for key,bad in [('task_owner',SID),('run_level',0),('logon_type',1),
                        ('multiple_instances',0),('enabled',False),
                        ('executable','D:/editable/python.exe'),
                        ('arguments','D:/editable/strategy.py')]:
            with self.subTest(key=key),self.assertRaises(bridge.BridgeError):
                bridge.validate_task(CONFIG,definition(**{key:bad}))

    def test_writable_or_extra_task_callers_are_rejected(self):
        for security in [f'D:P(A;;FA;;;{SID})',f'D:P(A;;GRGX;;;{SID})(A;;GRGX;;;AU)',
                         f'D:(A;;GRGX;;;{SID})', f'D:P(A;CI;GRGX;;;{SID})']:
            with self.subTest(security=security),self.assertRaises(bridge.BridgeError):
                bridge.validate_task(CONFIG,definition(security=security))

    def test_dispatch_does_not_overwrite_other_pending_or_active_request(self):
        with artifacts.scratch_directory('currency-wars-input-client-test') as run:
            ticket={'config':{**CONFIG,'inbox':str(run)},'request':{'request_id':'a'*32},
                    'dispatch_attempted':False,'instance_id':None}
            with patch.object(bridge,'task_rpc',return_value={'instances':[{'id':'other'}]}) as rpc:
                with self.assertRaises(bridge.BridgeError):bridge.dispatch(ticket)
                self.assertEqual(rpc.call_count,1)
                self.assertFalse(ticket['dispatch_attempted'])
            path=run/'launch.json';path.write_text('{"request_id":"other"}')
            with patch.object(bridge,'task_rpc',return_value={'instances':[]}) as rpc:
                with self.assertRaises(bridge.BridgeError):bridge.dispatch(ticket)
                self.assertEqual(rpc.call_count,1)
            self.assertEqual(json.loads(path.read_text()),{'request_id':'other'})

    def test_task_global_result_never_proves_request_exit(self):
        with artifacts.scratch_directory('currency-wars-input-client-test') as run:
            request={'request_id':'a'*32}
            ticket={'config':{**CONFIG,'inbox':str(run)},'request':request,
                    'dispatch_attempted':True,'instance_id':'{00000000-0000-0000-0000-000000000000}'}
            for global_result in (0,1,87,267009):
                bridge.write_object(run/'launch.json',request)
                with patch.object(bridge,'task_rpc',return_value={'instances':[],'last_result':global_result}):
                    with self.assertRaises(bridge.BridgeError):bridge.cancel_pending(ticket)
                self.assertFalse((run/'launch.json').exists())
                self.assertTrue(run.exists())

    def test_cancel_only_own_pending_and_no_attempt_needs_no_rpc(self):
        with artifacts.scratch_directory('currency-wars-input-client-test') as run:
            ticket={'config':{**CONFIG,'inbox':str(run)},'request':{'request_id':'a'*32},
                    'dispatch_attempted':False,'instance_id':None}
            bridge.write_object(run/'launch.json',{'request_id':'b'*32})
            with patch.object(bridge,'task_rpc') as rpc:
                self.assertEqual(bridge.cancel_pending(ticket)['state'],'not_launched')
                rpc.assert_not_called()
            self.assertEqual(bridge.read_object(run/'launch.json')['request_id'],'b'*32)

if __name__=='__main__':unittest.main(verbosity=2)
