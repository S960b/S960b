import os,sys,tempfile,socket,subprocess,secrets,time,urllib.request,urllib.error
from pathlib import Path
root=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
python=str(Path(sys.argv[2]).resolve()) if len(sys.argv)>2 else sys.executable
with tempfile.TemporaryDirectory(prefix='bridge-runtime-home-') as home:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    password=secrets.token_urlsafe(24)
    env=dict(os.environ,HOME=home,BRIDGE_DB_PATH=str(Path(home)/'pilot.db'),BRIDGE_HOST='127.0.0.1',BRIDGE_PORT=str(port),BRIDGE_BASE=base,BRIDGE_USER='owner',BRIDGE_PASS=password,BRIDGE_E2E_BASE=base,BRIDGE_E2E_PASS=password)
    with tempfile.TemporaryFile(mode='w+') as log:
        process=subprocess.Popen([python,str(root/'bridge_server.py')],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+15
            while True:
                if process.poll() is not None: raise RuntimeError('isolated server exited before readiness')
                try:
                    with urllib.request.urlopen(base+'/ping',timeout=.5) as response:
                        if response.status==200: break
                except (OSError,urllib.error.URLError):
                    if time.monotonic()>=deadline:raise RuntimeError('isolated server readiness deadline')
                    time.sleep(.05)
            result=subprocess.run([python,str(root/'e2e_test.py')],cwd=root,env=env,capture_output=True,text=True,timeout=40)
            print(result.stdout);print(result.stderr)
            print('ISOLATED_HTTP_E2E_EXIT=',result.returncode)
            sys.exit(result.returncode)
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
