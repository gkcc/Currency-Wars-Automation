"""Build a private, isolated CPython input component; no elevation or inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import PIL
import currency_wars_artifacts as artifacts
from currency_wars_broker_entry import PINNED, SOURCE
from currency_wars_input_bridge import INSTALL_ROOT, task_name
from currency_wars_bridge_task import RUNTIME_ROOT, INBOX, validate_config

PROJECT = Path(__file__).resolve().parent.parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def build(game_path):
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('The fixed input component requires CPython 3.12')
    game = Path(game_path).resolve(strict=True)
    if not game.is_file() or game.name.lower() != 'starrail.exe':
        raise RuntimeError('--game-path must name the installed StarRail.exe')
    # Package the unchanged task's approved paths. Building the package does
    # not require the caller's independent TEMP cache to point at that root.
    if digest(SOURCE) != PINNED:
        raise RuntimeError('Safety broker is not the independently reviewed source')
    sid = artifacts._windows_user_sid()
    installation = uuid.uuid4().hex
    repair_from = None
    if INSTALL_ROOT.exists():
        from currency_wars_input_bridge import InstallationAccess,verify_installation,read_object
        access=InstallationAccess()
        try:verify_installation(access)
        finally:access.close()
        previous=read_object(INSTALL_ROOT/'install.json')
        if previous['user_sid']!=sid or previous['task_name']!=task_name(sid):
            raise RuntimeError('An existing foreign component is not adopted')
        installation=previous['installation_id']
        repair_from={'installation_id':previous['installation_id'],'snapshot_sha256':digest(INSTALL_ROOT/'snapshot.json')}
    package_suffix=installation if repair_from is None else installation+'-repair-'+uuid.uuid4().hex
    package = Path('D:/Codex/Downloads') / ('CurrencyWarsInputBridge-' + package_suffix)
    package.mkdir(parents=True, exist_ok=False)
    payload = package / 'payload'
    (payload / 'python').mkdir(parents=True)
    base = Path(getattr(sys, '_base_executable', sys.executable)).parent
    for name in ('python.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        shutil.copy2(base / name, payload / 'python' / name)
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('This private runtime package requires the actual CPython 3.12 deployment')
    def ignore(folder, names):
        return [name for name in names if name in ('site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'tkinter', 'ensurepip')
                or name.endswith(('.pyc', '.pyo'))]
    shutil.copytree(base / 'Lib', payload / 'python/Lib', ignore=ignore)
    shutil.copytree(base / 'DLLs', payload / 'python/DLLs')
    shutil.copytree(Path(PIL.__file__).parent, payload / 'modules/PIL', ignore=ignore)
    (payload / 'code').mkdir()
    for name in ('currency_wars_control.py', 'currency_wars_bridge_task.py'):
        shutil.copy2(PROJECT / 'tools' / name, payload / 'code' / name)
    shutil.copy2(PROJECT / 'tools/install_input_bridge_admin.ps1', package / 'install.ps1')
    # The game executable itself is never changed. Its approved image is bound
    # here so a matching window title cannot authorize another elevated program.
    quoted_game = "'" + str(game).replace("'", "''") + "'"
    signature_script = ("$ErrorActionPreference='Stop';$env:PSModulePath=$PSHOME+'\\Modules';"
                        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
                        "$s=Get-AuthenticodeSignature -LiteralPath " + quoted_game + ";"
                        "@{status=$s.Status.ToString();subject=$s.SignerCertificate.Subject;"
                        "thumbprint=$s.SignerCertificate.Thumbprint}|ConvertTo-Json -Compress")
    signature = json.loads(subprocess.check_output([str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),
                                  '-NoProfile','-NonInteractive','-Command',signature_script],timeout=20,
                                  creationflags=subprocess.CREATE_NO_WINDOW).decode('utf-8-sig'))
    if signature['status']!='Valid' or 'miHoYo' not in signature['subject']:
        raise RuntimeError('The approved game executable does not have a valid miHoYo signature')
    config = dict(schema=1, installation_id=installation, user_sid=sid,
                  runtime_root=RUNTIME_ROOT, inbox=INBOX,
                  game_path=str(game), game_sha256=digest(game), broker_sha256=PINNED,
                  driver_sha256=digest(PROJECT/'tools/currency_wars_bridge_task.py'),
                  task_name=task_name(sid))
    validate_config(config)
    (payload / 'install.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf8')
    files = {str(p.relative_to(payload)).replace('\\','/'):digest(p)
             for p in sorted(payload.rglob('*')) if p.is_file()}
    # This independent file inventory stays inside the protected installation
    # and is verified by the ordinary client before each privileged task run.
    (payload/'snapshot.json').write_text(json.dumps({'schema':1,'files':files},indent=2),encoding='utf8')
    files['snapshot.json'] = digest(payload/'snapshot.json')
    manifest = dict(schema=1, install_root=str(INSTALL_ROOT), installation_id=installation, user_sid=sid,
                    task_name=config['task_name'], files=files,
                    task_arguments=subprocess.list2cmdline(['-I','-S','-B','-X','utf8',str(INSTALL_ROOT/'code/currency_wars_bridge_task.py')]),
                    installer_sha256=digest(package/'install.ps1'),
                    repair_from=repair_from,
                    project=str(PROJECT), source_hashes={name:digest(PROJECT/'tools'/name)
                        for name in ('currency_wars_control.py','currency_wars_bridge_task.py','install_input_bridge_admin.ps1')})
    path = package / 'package.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    receipt = dict(package=str(package), manifest_sha256=digest(path), installation_id=installation,
                   files=len(files), size_bytes=sum((payload/name).stat().st_size for name in files),
                   source_hashes=manifest['source_hashes'], owner='currency-wars-input-bridge-package',
                   game_authenticode=signature,game_sha256=config['game_sha256'],
                   chat_id=os.environ.get('CODEX_THREAD_ID'), state='built_not_installed')
    (PROJECT/'docs/INPUT_BRIDGE_PACKAGE.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(receipt,ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-path', required=True, help='Absolute path to the installed StarRail.exe')
    build(parser.parse_args().game_path)
