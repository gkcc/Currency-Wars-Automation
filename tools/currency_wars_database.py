"""Versioned offline Currency Wars catalogue. Never reads or controls a game process."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data/currency_wars/catalog.sqlite3'
KINDS = ('characters', 'bonds', 'equipment', 'investment_environments',
         'investment_strategies', 'opponents', 'enemy_units', 'enemy_affixes',
         'economy_rules', 'shop_odds')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports(
 id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, sources_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entities(
 id INTEGER PRIMARY KEY, import_id TEXT NOT NULL REFERENCES imports(id),
 kind TEXT NOT NULL, source_key TEXT NOT NULL, name TEXT NOT NULL,
 version TEXT, availability TEXT NOT NULL, cost INTEGER, position TEXT,
 role TEXT, quality TEXT, data_json TEXT NOT NULL,
 UNIQUE(import_id,kind,source_key));
CREATE INDEX IF NOT EXISTS entities_name ON entities(name,kind);
CREATE INDEX IF NOT EXISTS entities_scope ON entities(kind,availability,cost);
CREATE TABLE IF NOT EXISTS aliases(
 entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
 normalized_name TEXT NOT NULL, PRIMARY KEY(entity_id,normalized_name));
CREATE INDEX IF NOT EXISTS alias_lookup ON aliases(normalized_name);
CREATE TABLE IF NOT EXISTS character_bonds(
 character_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
 bond_name TEXT NOT NULL, PRIMARY KEY(character_id,bond_name));
CREATE INDEX IF NOT EXISTS member_bonds ON character_bonds(bond_name);
CREATE TABLE IF NOT EXISTS recipes(
 equipment_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
 variant INTEGER NOT NULL,
 ingredient_name TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0),
 PRIMARY KEY(equipment_id,variant,ingredient_name));
CREATE INDEX IF NOT EXISTS recipe_ingredients ON recipes(ingredient_name);
CREATE TABLE IF NOT EXISTS observations(
 key TEXT PRIMARY KEY, scope TEXT NOT NULL, data_json TEXT NOT NULL);
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def field(row, *keys):
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def integer(value):
    # Fees and quantities only: do not infer them from rarity or star count.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r'\d+(?:费)?', value.strip()):
        return int(value.strip().removesuffix('费'))
    return None


def normalize_name(value):
    return unicodedata.normalize('NFKC', value).translate(str.maketrans({'•': '·', '・': '·', '･': '·'})).strip()


def named_values(value):
    if value is None:
        return []
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, str):
        return [s.strip() for s in re.split(r'[,，、;；|]', value) if s.strip()]
    if isinstance(value, list):
        return [v if isinstance(v, str) else str(field(v, 'name', '名称'))
                for v in value if isinstance(v, str) or isinstance(v, dict) and field(v, 'name', '名称')]
    raise ValueError('invalid named list')


def effective_version(row, document):
    version = field(row, 'effective_rules_version', 'source_version')
    if not version and str(document.get('schema', '')).startswith('currency-wars-normalized-client/'):
        version = row.get('version')
    return version or document.get('version')


def availability(row, document, current_version='4.6'):
    explicit = field(row, 'availability', 'version_status', 'status')
    # Explicit exclusions win even if this row's release version is older/current.
    if explicit == 'future' or row.get('future') is True or row.get('is_future') is True or row.get('mentions_future_4_7') is True or 'future' in str(row.get('active_status', '')):
        return 'future'
    if explicit == 'inactive' or str(row.get('active_status', '')) in ('inactive', 'disabled'):
        return 'inactive'
    if explicit == 'historical' or row.get('active_status') == 'historical':
        return 'historical'
    effective = effective_version(row, document)
    if isinstance(effective, str) and re.fullmatch(r'\d+\.\d+(?:\.\d+)?', effective):
        ver = tuple(map(int, effective.split('.')))
        current = tuple(map(int, current_version.split('.')))
        if ver + (0,) * (3 - len(ver)) > current + (0,) * (3 - len(current)):
            return 'future'
        if ver + (0,) * (3 - len(ver)) < current + (0,) * (3 - len(current)):
            return 'historical'
    if explicit in ('current', 'future', 'historical', 'unknown', 'inactive'):
        if explicit == 'current' and not effective:
            return 'unknown'
        return explicit
    # Unknown source version must never be silently certified as current.
    default = document.get('default_availability', 'unknown')
    return 'unknown' if default == 'current' and not effective else default


def rows_for(document, kind):
    value = document.get(kind, document.get('opponent_affixes', []) if kind == 'enemy_affixes' else [])
    if isinstance(value, dict):
        return [dict(v, name=v.get('name', k)) if isinstance(v, dict)
                else {'name': k, 'value': v} for k, v in value.items()]
    if not isinstance(value, list):
        raise ValueError(f'{kind} must be a list or mapping')
    return value


def build(paths, output, current_version, season):
    prepared = []
    for path in paths:
        blob = path.read_bytes()
        doc = json.loads(blob.decode('utf-8-sig'))
        sha = hashlib.sha256(blob).hexdigest()
        namespace = str(doc.get('source_id', path.stem))
        if any(x[0] == namespace for x in prepared):
            raise ValueError('duplicate import namespace: ' + namespace)
        rows = []
        seen = set()
        for kind in KINDS:
            for index, row in enumerate(rows_for(doc, kind)):
                if not isinstance(row, dict):
                    raise ValueError(f'{namespace}/{kind}/{index}: expected object')
                name = field(row, 'name', '名称', '角色名', '羁绊名', '装备名')
                if not isinstance(name, str) or not name.strip():
                    raise ValueError(f'{namespace}/{kind}/{index}: missing name')
                raw_key = field(row, 'id', 'game_id', 'source_key')
                key = str(raw_key if raw_key is not None else name.strip())
                if (kind, key) in seen:
                    raise ValueError(f'duplicate source record: {namespace}/{kind}/{key}')
                seen.add((kind, key))
                # Validate all JSON before mutating an existing database.
                encoded(row)
                rows.append((kind, key, name.strip(), row))
        prepared.append((namespace, path, sha, doc, rows))
    if not prepared or not any(item[4] for item in prepared):
        raise ValueError('no catalogue records; existing database preserved')
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output)
    conn.execute('PRAGMA foreign_keys=ON')
    # executescript commits an existing transaction first. Start the transaction
    # INSIDE this script so both schema migration and catalogue replacement roll back.
    conn.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
    with conn:
        if 'variant' not in {r[1] for r in conn.execute('PRAGMA table_info(recipes)')}:
            conn.execute('DROP TABLE recipes')
            conn.execute('''CREATE TABLE recipes(equipment_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                variant INTEGER NOT NULL,ingredient_name TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),
                PRIMARY KEY(equipment_id,variant,ingredient_name))''')
            conn.execute('CREATE INDEX recipe_ingredients ON recipes(ingredient_name)')
        for table in ('character_bonds', 'recipes', 'aliases', 'entities', 'imports', 'observations', 'metadata'):
            conn.execute('DELETE FROM ' + table)
        for namespace, path, sha, doc, rows in prepared:
            conn.execute('INSERT INTO imports VALUES(?,?,?,?)',
                         (namespace, str(path.resolve()), sha, encoded(doc.get('sources', []))))
            for kind, key, name, row in rows:
                state = availability(row, doc, current_version)
                if state not in ('current', 'future', 'historical', 'unknown', 'inactive'):
                    raise ValueError('unsupported availability: ' + str(state))
                # BWIKI's version is often INTRODUCTION, not the effective rules snapshot.
                version = effective_version(row, doc)
                values = (namespace, kind, key, name, str(version) if version else None, state,
                          integer(field(row, 'cost', '费用')), field(row, 'position', '位置', '定位'),
                          field(row, 'role', '职责'), field(row, 'quality', '品质'), encoded(row))
                values = tuple(encoded(v) if isinstance(v, (dict, list)) else v for v in values)
                eid = conn.execute('''INSERT INTO entities(import_id,kind,source_key,name,version,
                    availability,cost,position,role,quality,data_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)''', values).lastrowid
                for alias in set([name, *named_values(row.get('aliases'))]):
                    conn.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)', (eid, normalize_name(alias)))
                if kind == 'characters':
                    for name_ in set(named_values(field(row, 'bonds', '羁绊'))):
                        conn.execute('INSERT INTO character_bonds VALUES(?,?)', (eid, name_))
                if kind == 'equipment':
                    recipe = field(row, 'recipe', 'ingredients', '配方')
                    variants = row.get('recipe_variants') or [recipe]
                    for variant_id, recipe in enumerate(variants):
                        ingredients = recipe.get('ingredients', recipe) if isinstance(recipe, dict) else recipe
                        if isinstance(ingredients, dict):
                            pairs = ingredients.items()
                        elif isinstance(ingredients, list):
                            counts = {}
                            for v in ingredients:
                                n = v if isinstance(v, str) else field(v, 'name', 'ingredient')
                                qty = 1 if isinstance(v, str) else integer(v.get('amount', 1))
                                if n and qty and qty > 0:
                                    counts[n] = counts.get(n, 0) + qty
                            pairs = counts.items()
                        else:
                            pairs = []
                        for name_, qty in pairs:
                            qty = integer(qty)
                            if qty and qty > 0:
                                conn.execute('INSERT INTO recipes VALUES(?,?,?,?)', (eid, variant_id, str(name_), qty))
            for key, data in doc.get('observations', {}).items():
                conn.execute('INSERT INTO observations VALUES(?,?,?)',
                             (namespace + ':' + key, 'historical; not current match state', encoded(data)))
        meta = {'schema': 'currency-wars-catalog/v1', 'built_at': datetime.now(timezone.utc).isoformat(),
                'client_version': current_version, 'season': season,
                'complete_current_game_verified': False,
                'policy': 'Future/unknown versions remain explicit; historical observations never override current match facts.'}
        for key, value in meta.items():
            conn.execute('INSERT INTO metadata VALUES(?,?)', (key, encoded(value)))
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()
    with open_db(output) as readback:
        return stats(readback)


def open_db(path):
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def stats(db):
    return {'metadata': {r['key']: json.loads(r['value_json']) for r in db.execute('SELECT * FROM metadata')},
            'counts': [dict(r) for r in db.execute('SELECT kind,availability,count(*) AS count FROM entities GROUP BY kind,availability ORDER BY kind,availability')],
            'relations': {t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0]
                          for t in ('character_bonds', 'recipes', 'observations')},
            'sources': [dict(r) for r in db.execute('SELECT id,path,sha256 FROM imports')],
            'integrity': db.execute('PRAGMA integrity_check').fetchone()[0]}


def compact_data(value, star=None):
    if isinstance(value, dict):
        return {k: compact_data(v, star) for k, v in value.items()
                if k not in ('raw', 'raw_fields', 'raw_wikitext', 'raw_description', 'semantic_fields', 'numeric_tokens')}
    if isinstance(value, list):
        if star is not None and value and all(isinstance(v, dict) and 'star' in v for v in value):
            value = [v for v in value if v['star'] == star]
        return [compact_data(v, star) for v in value]
    return value


def records(db, name=None, kind=None, state=None, cost=None, limit=200, full=False):
    where, args = [], []
    if name is not None:
        where.append('id IN (SELECT entity_id FROM aliases WHERE normalized_name=?)')
        args.append(normalize_name(name))
    for col, val in [('kind', kind), ('availability', state), ('cost', cost)]:
        if val is not None:
            where.append(col + '=?')
            args.append(val)
    sql = 'SELECT * FROM entities' + (' WHERE ' + ' AND '.join(where) if where else '')
    rows = db.execute(sql + ' ORDER BY kind,name,id LIMIT ?', [*args, limit]).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        data = json.loads(item.pop('data_json'))
        if full:
            item['data'] = data
            sources = json.loads(db.execute('SELECT sources_json FROM imports WHERE id=?', (r['import_id'],)).fetchone()[0])
            ids = data.get('source_ids') or [data.get('source_id')]
            item['source'] = {k: sources[k] for k in ids if k in sources} if isinstance(sources, dict) and any(k in sources for k in ids) else sources
        else:
            item['bonds'] = [b[0] for b in db.execute('SELECT bond_name FROM character_bonds WHERE character_id=? ORDER BY bond_name', (r['id'],))]
        result.append(item)
    return result


def team(db, names):
    # One source snapshot per unit. Duplicate names cannot double-count a bond.
    selected, unknown, seen_entities = [], [], set()
    seen_names = set()
    for name in names:
        if normalize_name(name) in seen_names:
            continue
        seen_names.add(normalize_name(name))
        rows = records(db, name=name, kind='characters', full=True)
        current = [r for r in rows if r['availability'] == 'current']
        candidates = current or [r for r in rows if r['availability'] in ('unknown', 'historical')]
        if len(candidates) != 1:
            unknown.append({'name': name, 'reason': 'missing or ambiguous source snapshot', 'candidate_count': len(candidates)})
        else:
            if candidates[0]['id'] in seen_entities:
                continue
            seen_entities.add(candidates[0]['id'])
            selected.append(candidates[0])
            if candidates[0]['availability'] != 'current':
                unknown.append({'name': name, 'reason': 'source not certified current',
                                'availability': candidates[0]['availability']})
    counts = {}
    for r in selected:
        for b in set(named_values(field(r['data'], 'bonds', '羁绊'))):
            counts[b] = counts.get(b, 0) + 1
    return {'characters': selected, 'bonds': [{'name': name, 'members': n,
             'definitions': [r for r in records(db, name=name, kind='bonds', full=True)
                             if r['availability'] not in ('future', 'inactive')]} for name, n in sorted(counts.items())],
            'needs_review': unknown,
            'scope': 'Static named-member count only; actual deployed slots, items, and investments must supply runtime overrides.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path, default=DEFAULT_DB)
    subs = ap.add_subparsers(dest='command', required=True)
    b = subs.add_parser('build')
    b.add_argument('--input', type=Path, action='append', required=True)
    b.add_argument('--current-version', default='4.6')
    b.add_argument('--season', default='4.4')
    subs.add_parser('stats')
    show = subs.add_parser('show')
    show.add_argument('name')
    show.add_argument('--kind', choices=KINDS)
    show.add_argument('--star', type=int, choices=(1, 2, 3, 4))
    show.add_argument('--raw', action='store_true', help='Include original fields; default is compact resolved catalogue data.')
    show.add_argument('--references', action='store_true', help='Include secondary, historical, or uncertain snapshots.')
    ls = subs.add_parser('list')
    ls.add_argument('kind', choices=KINDS)
    ls.add_argument('--availability', choices=('current', 'future', 'historical', 'unknown', 'inactive'))
    ls.add_argument('--cost', type=int)
    ls.add_argument('--limit', type=int, default=200)
    tm = subs.add_parser('team')
    tm.add_argument('names', nargs='+')
    rec = subs.add_parser('recipes')
    rec.add_argument('ingredient')
    a = ap.parse_args()
    if a.command == 'build':
        out = build(a.input, a.db, a.current_version, a.season)
    else:
        with open_db(a.db) as db:
            if a.command == 'stats':
                out = stats(db)
            elif a.command == 'show':
                out = records(db, name=a.name, kind=a.kind, full=True)
                if not a.references and any(r['availability'] == 'current' for r in out):
                    out = [r for r in out if r['availability'] == 'current']
                if not a.raw:
                    for row in out:
                        row['data'] = compact_data(row['data'], a.star)
            elif a.command == 'list':
                if not 1 <= a.limit <= 2000:
                    raise ValueError('limit must be 1..2000')
                out = records(db, kind=a.kind, state=a.availability, cost=a.cost, limit=a.limit)
            elif a.command == 'team':
                out = team(db, a.names)
            else:
                rows = db.execute('''SELECT e.id,e.import_id,e.name,e.version,e.availability,r.variant
                    FROM recipes r JOIN entities e ON e.id=r.equipment_id WHERE r.ingredient_name=?
                    ORDER BY e.name,e.id,r.variant''', (a.ingredient,)).fetchall()
                out = []
                for row in rows:
                    item = dict(row)
                    item['ingredients'] = [dict(r) for r in db.execute('''SELECT ingredient_name AS name,amount
                        FROM recipes WHERE equipment_id=? AND variant=? ORDER BY ingredient_name''', (row['id'], row['variant']))]
                    out.append(item)
    print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
