"""Offline regression tests. No local user systemd or network access."""
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import proxy35hi as p

class URL(unittest.TestCase):
    def test_valid_local_proxy(self):
        self.assertEqual(p.proxy_url('http://127.0.0.1:43210/v1/mcp/tunnel_test_xyz'),
                         (43210,'/v1/mcp/tunnel_test_xyz'))
    def test_reject_nonloopback(self):
        for url in ('http://0.0.0.0:10001/v1/mcp/tunnel_ok',
                    'http://localhost:12345/v1/mcp/tunnel_ok',
                    'http://192.0.2.1:12345/v1/mcp/tunnel_ok',
                    'https://127.0.0.1:12345/v1/mcp/tunnel_ok',
                    'http://127.0.0.1:12345.attacker/v1/mcp/tunnel_ok'):
            with self.subTest(url=url),self.assertRaises(p.Stop):p.proxy_url(url)
    def test_reject_bad_paths(self):
        for url in ('http://127.0.0.1:9000/mcp',
                    'http://127.0.0.1:9000/v1/mcp/xx?token=abc',
                    'http://127.0.0.1:9000/v1/mcp/../../private',
                    'http://127.0.0.1:80/v1/mcp/tunnel_ok',
                    'http://127.0.0.1:9000/v1/mcp/tunnel_ok#private'):
            with self.subTest(url=url),self.assertRaises(p.Stop):p.proxy_url(url)

class LocalProxyJSON(unittest.TestCase):
    def test_pretty_printed_json_and_fragmented_writes(self):
        readfd,writefd=os.pipe()
        obj={'tunnel_id':'tunnel_local','mcp_url':'http://127.0.0.1:45678/v1/mcp/tunnel_local',
             'control_plane_transport':'unix','backend':'go-in-memory'}
        blob=json.dumps(obj,indent=2).encode()+b'\n'
        def writer():
            with os.fdopen(writefd,'wb',buffering=0) as stream:
                for part in (blob[:11],blob[11:24],blob[24:]):
                    stream.write(part);time.sleep(.008)
        th=threading.Thread(target=writer)
        th.start()
        with os.fdopen(readfd,'rb',buffering=0) as stream:
            fake=SimpleNamespace(stdout=stream,poll=lambda:None)
            self.assertEqual(p.wait_proxy_url(fake,seconds=2),(45678,'/v1/mcp/tunnel_local'))
        th.join(timeout=2)
        self.assertFalse(th.is_alive())
    def test_pretty_printed_json_rejects_external_url(self):
        readfd,writefd=os.pipe()
        payload=json.dumps({'mcp_url':'http://192.0.2.4:12345/v1/mcp/tunnel_local',
                            'control_plane_transport':'tcp'},indent=2).encode()
        os.write(writefd,payload);os.close(writefd)
        with os.fdopen(readfd,'rb',buffering=0) as stream:
            fake=SimpleNamespace(stdout=stream,poll=lambda:None)
            with self.assertRaises(p.Stop):p.wait_proxy_url(fake,seconds=2)

class Files(unittest.TestCase):
    def test_private_and_hash_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'key';path.write_bytes(b'k'*64);path.chmod(0o600)
            self.assertTrue(p.safe_file(path,hashlib.sha256(b'k'*64).hexdigest()))
            self.assertFalse(p.safe_file(path,'0'*64))
            path.chmod(0o644);self.assertFalse(p.safe_file(path))
            path.chmod(0o600)
            (Path(tmp)/'symlink').symlink_to(path)
            self.assertFalse(p.safe_file(Path(tmp)/'symlink'))
            os.link(path,Path(tmp)/'hard')
            self.assertFalse(p.safe_file(path))
    def test_exclusive_one_shot_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'attempt'
            p.private_create(path,b'hello')
            self.assertEqual(path.read_bytes(),b'hello')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            with self.assertRaises(FileExistsError):p.private_create(path,b'new')
            self.assertEqual(path.read_bytes(),b'hello')
    def test_all_source_files(self):
        self.assertEqual(len(p.SOURCES),6)
        src=Path(__file__).read_text()
        self.assertNotIn('CONTROL_PLANE_API_KEY=',Path(p.__file__).read_text())
        self.assertNotIn('os.system(',Path(p.__file__).read_text())

class Transport(unittest.TestCase):
    def test_only_read_tool_calls(self):
        source=Path(p.__file__).read_text()
        self.assertIn("('managed_health',{},172)",source)
        self.assertIn("('managed_list',{'offset':0,'limit':10},173)",source)
        self.assertNotIn("rpc_request(port,path,'tools/call',{'name':'managed_create'",source)
        self.assertNotIn("rpc_request(port,path,'tools/call',{'name':'managed_send_task'",source)
    def test_keys_never_forwarded_by_test_client(self):
        # The test client must deliberately omit the local key so success
        # proves that the real tunnel-client injected it from a file.
        source=Path(p.__file__).read_text()
        self.assertIn("headers={'Accept':'application/json, text/event-stream'",source)
        begin=source.index('def rpc_request(')
        end=source.index('\ndef exercise(',begin)
        self.assertNotIn("headers['X-Bridge-Local-Key']",source[begin:end])
        self.assertNotIn("'X-Bridge-Local-Key':secret",source[begin:end])
    def test_actual_file_reference_and_disabled_discovery(self):
        s=Path(p.__file__).read_text()
        self.assertIn("'MCP_EXTRA_HEADERS':'X-Bridge-Local-Key: file:'+str(KEY)",s)
        self.assertIn("'MCP_OAUTH_DISCOVERY':'disabled'",s)
        self.assertIn("'dev','proxy'",s)
        self.assertIn("'--listen','127.0.0.1:0'",s)
        self.assertNotIn("'run','--health",s)
        self.assertNotIn("'--control-plane.tunnel-id'",s)
    def test_proxy_terminates_process_group(self):
        class Fake:
            pid=1234
            def __init__(self):self.count=0
            def poll(self):return None
            def wait(self,timeout):return 0
        f=Fake()
        with patch.object(os,'killpg') as kill:
            self.assertEqual(p.proc_terminated(f),'sigterm')
            kill.assert_called_once_with(1234,p.signal.SIGTERM)
    def test_proxy_kill_timeout_marker(self):
        class Fake:
            pid=1234
            def poll(self):return None
            def wait(self,timeout):
                if timeout==8:raise subprocess.TimeoutExpired('dev proxy',timeout)
                return 0
        f=Fake()
        with patch.object(os,'killpg') as kill:
            self.assertEqual(p.proc_terminated(f),'sigkill')
            self.assertEqual(kill.call_count,2)
    def test_no_external_tunnel_key_in_dev_env(self):
        source=Path(p.__file__).read_text()
        begin=source.index("        env={'PATH':")
        end=source.index("        args=[",begin)
        self.assertNotIn('CONTROL_PLANE_API_KEY',source[begin:end])
        self.assertNotIn('CONTROL_PLANE_TUNNEL_ID',source[begin:end])
        self.assertIn("'NO_PROXY':'127.0.0.1,localhost'",source[begin:end])
    def test_list_has_no_extra_tools(self):
        self.assertEqual(len(p.TOOLS),11)
        self.assertTrue({'managed_health','managed_list'}<=p.TOOLS)
        self.assertIn('managed_send_task',p.TOOLS)

class Candidate(unittest.TestCase):
    def test_candidate_protected_from_accidental_launch(self):
        t=(Path(__file__).parent/'run_stage35hi_candidate_NOT_ACTIVATED.sh').read_text()
        self.assertIn('STAGE35HI_EXPLICITLY_ARMED_BY_FUTURE_PACKAGE',t)
        self.assertIn('STAGE35I_CANDIDATE_BLOCKED_NOT_ARMED',t)
        self.assertIn("MCP_EXTRA_HEADERS='X-Bridge-Local-Key: file:",t)
        self.assertIn("MCP_SERVER_URL='http://127.0.0.1:18777/mcp'",t)
        self.assertIn('127.0.0.1:18778',t)
        self.assertNotIn('systemctl --user stop',t)
    def test_candidate_cannot_run_without_future_stage(self):
        t=Path(__file__).parent/'run_stage35hi_candidate_NOT_ACTIVATED.sh'
        r=subprocess.run(['bash',str(t)],capture_output=True,text=True,timeout=3,
                         env={'PATH':'/usr/bin:/bin','HOME':'/tmp'})
        self.assertNotEqual(r.returncode,0)
        self.assertIn('CANDIDATE_BLOCKED_NOT_ARMED',r.stderr)
    def test_manifest_and_install_are_one_shot(self):
        t=(Path(__file__).parent/'170_install_and_run_once.sh').read_text()
        self.assertIn('TARGET_ALREADY_EXISTS_DO_NOT_REINSTALL',t)
        self.assertIn('proxy35hi.py once',t)
        self.assertNotIn('enable --now',t)
        self.assertNotIn('restart',t)
        self.assertNotIn('hermes-pg-tunnel-stage33fd.service stop',t)

if __name__=='__main__':unittest.main()
