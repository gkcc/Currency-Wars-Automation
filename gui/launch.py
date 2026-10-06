"""Lifecycle helper for the real Rust/Tauri EXE; it never renders a Python UI."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import currency_wars_artifacts as artifacts
import currency_wars_broker_entry as entry
import currency_wars_input_bridge as input_bridge
import psutil
from processes import stop_identity
from currency_wars_source_guard import activity, mutation_lock
from currency_wars_update import launch_prerequisites, update, write_status

PROJECT=Path(__file__).resolve().parent.parent


def register_runtime_location(runtime, location, chat, child_pid, lease, records):
    """Publish only after both owners have recorded the actual native child."""
    state, identity=artifacts.process_identity(child_pid)
    if state!='active' or not identity or not identity.startswith('windows:'):
        raise RuntimeError('Native GUI process creation identity is unavailable')
    records[(child_pid,identity)]={'pid':child_pid,'process_identity':identity}
    lease.children(list(records.values()),complete=False)
    artifacts.protect_children(runtime,list(records.values()),root=runtime.parent,complete=False)
    marker=artifacts.read_marker(runtime,root=runtime.parent)
    if (marker['pid']!=os.getpid()
            or artifacts.process_identity(os.getpid())!=('active',marker['process_identity'])
            or artifacts.process_identity(child_pid)!=('active',identity)):
        raise RuntimeError('GUI launch identities changed before publication')
    input_bridge.write_object(runtime/'runtime-location.json',{
        'schema':1,'owner':'currency-wars-gui-runtime','run_id':marker['run_id'],
        'chat_id':chat,'runtime_location':location,'launcher_pid':marker['pid'],
        'launcher_creation_id':marker['process_identity'].split(':',1)[1],
        'gui_pid':child_pid,'gui_creation_id':identity.split(':',1)[1]})
    return identity


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary')
    parser.add_argument('--chat-id',default=os.environ.get('CODEX_THREAD_ID') or uuid.uuid4().hex)
    parser.add_argument('--test-mode',action='store_true')
    parser.add_argument('--test-runner')
    parser.add_argument('--project-dir')
    parser.add_argument('--debug-port')
    parser.add_argument('--skip-update', action='store_true')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--floating',action='store_true',help='Open the same GUI as a compact always-on-top control panel')
    args=parser.parse_args()
    binary=Path(args.binary) if args.binary else PROJECT/'gui/bin/currency-wars-gui.exe'
    if not binary.is_file():raise RuntimeError('Rust/Tauri executable has not been built')
    project=Path(args.project_dir).resolve() if args.project_dir else PROJECT
    if not args.test_mode and not args.skip_update:
        status=update(project)
        write_status(project,status)
        print(json.dumps(status,ensure_ascii=False),flush=True)
        if status.get('applied'):
            os.execv(sys.executable,[sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),*sys.argv[1:],'--skip-update'])
    (project / 'docs').mkdir(exist_ok=True)
    records={}
    child=None
    runtime=None
    with mutation_lock(project):
        if not args.test_mode:
            reason=launch_prerequisites(project)
            if reason:raise RuntimeError(reason)
        lifecycle=activity(project,'gui')
        lease=lifecycle.__enter__()
    try:
      location=input_bridge.runtime_location(entry.PINNED)
      with artifacts.scratch_directory('currency-wars-native-gui-'+os.environ.get('CODEX_THREAD_ID','local')[:8],root=Path(location['runtime_root'])) as runtime:
        try:
            command=[str(binary),'--runtime-dir',str(runtime),'--chat-id',args.chat_id,'--project-dir',str(project),'--python',sys.executable,
                     '--runtime-location-json',json.dumps(location,ensure_ascii=False)]
            if args.test_mode:command.append('--test-mode')
            if args.test_runner:command+=['--test-runner',args.test_runner]
            if args.debug_port:command+=['--debug-port',args.debug_port]
            if args.start:command.append('--start')
            if args.floating:command.append('--floating')
            with (runtime/'native.stdout.log').open('wb') as output,(runtime/'native.stderr.log').open('wb') as error:
                lease.children([],complete=False)
                artifacts.protect_children(runtime,[],root=runtime.parent,complete=False)
                child=subprocess.Popen(command,stdout=output,stderr=error,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                identity=register_runtime_location(runtime,location,args.chat_id,child.pid,lease,records)
                print(json.dumps({'gui_runtime':str(runtime),'native_pid':child.pid,'native_identity':identity,'framework':'Rust/Tauri'},ensure_ascii=False),flush=True)
                while child.poll() is None:
                    try:
                        for candidate in psutil.Process(child.pid).children(recursive=True):
                            # The runner/broker are never GUI-owned; only its WebView processes.
                            if candidate.name().lower()!='msedgewebview2.exe':continue
                            state,created=artifacts.process_identity(candidate.pid)
                            if state=='active' and created:records[(candidate.pid,created)]={'pid':candidate.pid,'process_identity':created}
                    except (psutil.NoSuchProcess,psutil.AccessDenied):pass
                    live=[record for record in records.values() if artifacts.process_identity(record['pid'])==('active',record['process_identity'])]
                    artifacts.protect_children(runtime,live,root=runtime.parent,complete=False)
                    lease.children(list(records.values()),complete=False)
                    time.sleep(.5)
        finally:
            if child and child.poll() is None:
                child.kill();child.wait(timeout=5)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and any(artifacts.process_identity(record['pid'])==('active',record['process_identity']) for record in records.values()):time.sleep(.1)
            for record in records.values():
                if artifacts.process_identity(record['pid'])==('active',record['process_identity']):stop_identity(record['pid'],record['process_identity'])
            artifacts.protect_children(runtime,[],root=runtime.parent,complete=True)
            lease.children([],complete=True)
    finally:
        lifecycle.__exit__(None,None,None)
    print(json.dumps({'gui_runtime':str(runtime),'removed':not runtime.exists(),'owned_processes':len(records),'game_inputs':0},ensure_ascii=False),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        import ctypes
        ctypes.WinDLL('user32').MessageBoxW(None,str(error),'Currency Wars startup',0x10)
        raise
