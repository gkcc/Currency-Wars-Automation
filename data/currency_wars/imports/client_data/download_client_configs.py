"""Download only public static Config JSONs referenced by released mode tables."""
import collections
import concurrent.futures
import json

from download_client_data import ROOT, api, atomic_json, download


def main():
    label = 'release_4_6'
    release = json.loads((ROOT / label / 'source_revision.json').read_text(encoding='utf-8'))
    pointers = set()
    def find(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('JsonPath', 'JsonOverrideConfig', 'TraitEffectJson') and isinstance(item, str) and item.startswith('Config/') and item.endswith('.json'):
                    pointers.add(item)
                else:
                    find(item)
        elif isinstance(value, list):
            for item in value:
                find(item)
    for path in (ROOT / label / 'ExcelOutput').glob('GridFight*.json'):
        find(json.loads(path.read_text(encoding='utf-8')))
    monsters = {row['MonsterID']: row for row in json.loads((ROOT / label / 'ExcelOutput/MonsterConfig.json').read_text(encoding='utf-8'))}
    templates = {row['MonsterTemplateID']: row for row in json.loads((ROOT / label / 'ExcelOutput/MonsterTemplateConfig.json').read_text(encoding='utf-8'))}
    for row in json.loads((ROOT / label / 'ExcelOutput/GridFightMonster.json').read_text(encoding='utf-8')):
        template = templates.get(monsters.get(row['MonsterID'], {}).get('MonsterTemplateID'), {})
        if template.get('JsonConfig'):
            pointers.add(template['JsonConfig'])
    trees = {}
    def tree(sha):
        if sha not in trees:
            trees[sha] = api('/git/trees/' + sha)
        return trees[sha]
    groups = collections.defaultdict(list)
    for path in pointers:
        groups['/'.join(path.split('/')[:3])].append(path)
    entries = {}
    inventory = []
    for prefix, paths in sorted(groups.items()):
        sha = release['Config_tree_sha']
        for part in prefix.split('/')[1:]:
            sha = next(row['sha'] for row in tree(sha)['tree'] if row['path'] == part and row['type'] == 'tree')
        result = api('/git/trees/' + sha + '?recursive=1')
        if result.get('truncated'):
            raise ValueError('Config subtree inventory truncated: ' + prefix)
        inventory.append({'prefix': prefix, 'tree_sha': sha, 'truncated': False, 'entry_count': len(result['tree'])})
        available = {prefix + '/' + row['path']: row for row in result['tree'] if row['type'] == 'blob'}
        for path in paths:
            if path in available:
                entries[path] = available[path]
    missing = sorted(pointers - entries.keys())
    jobs = [(label, release['version'], release['revision'], path, entries[path]) for path in sorted(entries)]
    files, errors = [], []
    print(json.dumps({'referenced_config_count': len(pointers), 'available_count': len(jobs),
                      'missing_count': len(missing), 'missing_sample': missing[:5]}), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for number, result in enumerate(pool.map(download, jobs), 1):
            (errors if result.get('error') else files).append(result)
            if number % 100 == 0 or number == len(jobs):
                print(json.dumps({'config_downloaded': number, 'errors': len(errors)}), flush=True)
    atomic_json(ROOT / 'config_manifest.json', {'release': release, 'subtree_inventory': inventory,
                'referenced_config_count': len(pointers), 'files': files, 'errors': errors, 'missing_config_paths': missing})
    print(json.dumps({'finished': True, 'files': len(files), 'errors': len(errors), 'bytes': sum(row['bytes'] for row in files)}), flush=True)


if __name__ == '__main__':
    main()
