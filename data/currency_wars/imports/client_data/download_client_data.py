"""Download pinned public release data. No game process or desktop access."""
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
REPOSITORY = 'DimbreathBot/TurnBasedGameData'
API = 'https://api.github.com/repos/' + REPOSITORY
SPECS = [
    ('release_4_6', '4.6.0', '312b4597691e0a29cf9828a15b592757dba86949'),
    ('release_4_4', '4.4.0', 'b11066beacc4de454b625fafc7ea3dd540c5bbf3'),
]
SUPPORT = {
    'AvatarConfig.json', 'AvatarRankConfig.json', 'AvatarSkillConfig.json',
    'AvatarPropertyConfig.json', 'AvatarDamageType.json', 'AvatarBaseType.json',
    'MonsterConfig.json', 'MonsterSkillConfig.json', 'MonsterTemplateConfig.json',
    'MonsterDifficultyConfig.json', 'MonsterEliteConfig.json',
    'MonsterPropertyConfig.json', 'BattleEventConfig.json', 'MazeBuff.json',
    'StageConfig.json', 'ExtraEffectConfig.json', 'ActivityModule.json',
    'AvatarConfigLD.json', 'AvatarRankConfigLD.json', 'AvatarSkillConfigLD.json',
    'MonsterConfigLD.json', 'MonsterSkillConfigLD.json', 'MonsterTemplateConfigLD.json',
}


def request(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'CurrencyWarsStaticDatabase/1.0'})
    with urllib.request.urlopen(req, timeout=40) as response:
        return response.read(), dict(response.headers)


def api(path):
    return json.loads(request(API + path)[0])


def blob_sha(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def download(spec):
    label, version, revision, relative, tree_entry = spec
    output = ROOT / label / relative
    url = f'https://raw.githubusercontent.com/{REPOSITORY}/{revision}/{relative}'
    try:
        if output.is_file() and blob_sha(output.read_bytes()) == tree_entry['sha']:
            raw = output.read_bytes()
        else:
            raw = request(url)[0]
        if blob_sha(raw) != tree_entry['sha']:
            raise ValueError('Downloaded bytes do not match pinned Git blob')
        data = json.loads(raw) if relative.endswith('.json') else None
        if not output.is_file() or output.read_bytes() != raw:
            output.parent.mkdir(parents=True, exist_ok=True)
            temp = output.with_name(output.name + '.' + str(os.getpid()) + '.tmp')
            try:
                temp.write_bytes(raw)
                temp.replace(output)
            finally:
                if temp.exists():
                    temp.unlink()
        fields = sorted({key for row in data for key in row}) if isinstance(data, list) and all(isinstance(x, dict) for x in data) else []
        return {'relative_path': label + '/' + relative, 'client_path': relative,
                'version': version, 'repository_revision': revision, 'source_url': url,
                'git_blob_sha1': tree_entry['sha'], 'sha256': hashlib.sha256(raw).hexdigest(),
                'bytes': len(raw), 'shape': type(data).__name__ if data is not None else 'text',
                'record_count': len(data) if isinstance(data, (dict, list)) else None,
                'fields': fields}
    except Exception as error:
        return {'relative_path': label + '/' + relative, 'source_url': url, 'error': str(error)}


def main():
    manifest = {'schema': 'currency-wars-pinned-client-import-v1',
                'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'repository': REPOSITORY, 'repository_url': 'https://github.com/' + REPOSITORY,
                'candidate_current_release': 'release_4_6', 'baseline_release': 'release_4_4',
                'future_data_policy': 'Only OSPRODWin4.6.0 and OSPRODWin4.4.0 release commits are downloaded; beta 4.6.51 and future 4.7 sources are excluded. Runtime availability still requires season/module gating.',
                'releases': [], 'files': [], 'download_errors': []}
    for label, version, revision in SPECS:
        commit = api('/commits/' + revision)
        build = commit['commit']['message']
        if not build.startswith('OSPRODWin' + version + '_'):
            raise ValueError('Commit is not the requested production release: ' + build)
        roots = {item['path']: item for item in api('/git/trees/' + revision)['tree']}
        excel = api('/git/trees/' + roots['ExcelOutput']['sha'])
        textmap = api('/git/trees/' + roots['TextMap']['sha'])
        if excel.get('truncated') or textmap.get('truncated'):
            raise ValueError('Release table or text map inventory is truncated')
        atomic_json(ROOT / label / 'excel_tree_inventory.json', {'revision': revision,
                    'tree_sha': roots['ExcelOutput']['sha'],
                    'source_url': API + '/git/trees/' + roots['ExcelOutput']['sha'], **excel})
        selected = [('ExcelOutput/' + item['path'], item) for item in excel['tree']
                    if item['type'] == 'blob' and (item['path'].startswith('GridFight') or item['path'] in SUPPORT)]
        selected += [('TextMap/' + item['path'], item) for item in textmap['tree']
                     if item['path'] in {'TextMapCHS.json', 'TextMapMainCHS.json'}]
        readme = roots.get('README.md')
        if readme:
            selected.append(('README.md', readme))
        manifest['releases'].append({'label': label, 'version': version, 'revision': revision,
                                     'commit_url': commit['html_url'], 'commit_message': build,
                                     'committed_at': commit['commit']['committer']['date'],
                                     'ExcelOutput_tree_sha': roots['ExcelOutput']['sha'],
                                     'TextMap_tree_sha': roots['TextMap']['sha'],
                                     'Config_tree_sha': roots['Config']['sha'],
                                     'selected_file_count': len(selected)})
        atomic_json(ROOT / label / 'source_revision.json', manifest['releases'][-1])
        print(json.dumps({'started_release': label, 'files': len(selected)}, ensure_ascii=False), flush=True)
        jobs = [(label, version, revision, path, entry) for path, entry in selected]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for index, result in enumerate(pool.map(download, jobs), 1):
                if result.get('error'):
                    manifest['download_errors'].append(result)
                else:
                    manifest['files'].append(result)
                if index % 25 == 0 or index == len(jobs):
                    print(json.dumps({'release': label, 'completed_files': index, 'errors': len(manifest['download_errors'])}), flush=True)
        atomic_json(ROOT / 'manifest.json', manifest)
    print(json.dumps({'files': len(manifest['files']), 'errors': len(manifest['download_errors']),
                      'bytes': sum(item['bytes'] for item in manifest['files'])}), flush=True)


if __name__ == '__main__':
    main()
