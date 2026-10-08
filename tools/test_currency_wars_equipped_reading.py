"""Synthetic OCR/PNG contracts only; no real equipped-owner positive evidence."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from currency_wars_perception import Perception, READ_CONTRACT_VERSION
from currency_wars_state_reader import StateReader, TOOLTIP_SCHEMA


def row(text, box, confidence=.99):
    return dict(text=text, raw_text=text, confidence=confidence, box=list(box), normalization_basis=None)


def tooltip_rows(name='示例装备Lv.2', item_type='进阶装备'):
    return [row(name, [1200, 300, 1400, 335]), row(item_type, [1200, 353, 1320, 380])]


def raw_ocr(rows):
    return [([[r['box'][0] / 1.5, r['box'][1] / 1.5], [r['box'][2] / 1.5, r['box'][1] / 1.5],
              [r['box'][2] / 1.5, r['box'][3] / 1.5], [r['box'][0] / 1.5, r['box'][3] / 1.5]],
             r['raw_text'], r['confidence']) for r in rows]


def write_image(path, size=(1920, 1080), color=(90, 90, 90)):
    with Image.new('RGB', size, color) as image:
        image.save(path)


class EquippedTooltipReadingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cw-equipment-tooltip-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'synthetic.png'
        write_image(self.path)
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def read(self, rows=None, **kwargs):
        return StateReader.read_tooltips(self.path, rows=tooltip_rows() if rows is None else rows,
            page=kwargs.pop('page', 'unknown'), rows_snapshot_id=kwargs.pop('rows_snapshot_id', self.digest), **kwargs)

    def test_native_candidate_keeps_raw_title_and_never_assigns_owner_or_slot(self):
        rows = tooltip_rows()
        original = copy.deepcopy(rows)
        with patch.object(StateReader, '_load', side_effect=AssertionError('roster resources must not be loaded')):
            result = self.read(rows)
        self.assertEqual(result['schema'], TOOLTIP_SCHEMA)
        self.assertEqual(result['snapshot_id'], self.digest)
        self.assertEqual(result['input'], dict(sha256=self.digest, format='PNG', size=[1920, 1080]))
        self.assertEqual(result['rows_snapshot_id'], self.digest)
        candidate, = result['candidates']
        self.assertEqual((candidate['name'], candidate['item_type'], candidate['wearable_type']),
                         ('示例装备Lv.2', '进阶装备', True))
        self.assertEqual(candidate['evidence'], dict(title=rows[0], type_anchor=rows[1]))
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['checked'])
        for value in (result, candidate):
            for field in ('equipped', 'owned', 'location', 'owner', 'slot'):
                self.assertIsNone(value[field])
        self.assertIn('actual_unit_panel_source_missing', result['reasons'])
        self.assertIn('actual_equipment_slot_source_missing', result['reasons'])
        self.assertEqual(rows, original)
        candidate['evidence']['title']['text'] = 'changed copy'
        self.assertEqual(rows, original)

    def test_consumable_and_unit_candidates_do_not_become_equipped_items(self):
        consumable, = self.read(tooltip_rows(item_type='消耗品'))['candidates']
        self.assertFalse(consumable['wearable_type'])
        self.assertIsNone(consumable['equipped'])
        unit, = self.read(tooltip_rows(name='示例角色Lv.9', item_type='前台'))['candidates']
        self.assertEqual((unit['kind'], unit['name'], unit['position']), ('unit', '示例角色', '前台'))
        self.assertIsNone(unit['item_type'])
        self.assertIsNone(unit['wearable_type'])
        self.assertIsNone(unit['owner'])

    def test_bad_boxes_scores_and_raw_normalizations_remain_unknown(self):
        faults = [{'confidence': value} for value in (.8999, 1.01, True, '0.99', float('nan'))]
        faults += [{'box': box} for box in ([0, 1, 0, 2], [-1, 2, 4, 5], [0, 2, 1921, 5],
                                          [1200, 300, 1400, True], [1200, 300, 1400], 'box')]
        faults += [{'raw_text': '其他原文'}, {'normalization_basis': 'declared alias'},
                   {'confidence': .9, 'raw_confidence': .89996}]
        for fault in faults:
            for index in (0, 1):
                rows = tooltip_rows()
                rows[index].update(fault)
                with self.subTest(fault=fault, index=index):
                    self.assertEqual(self.read(rows)['candidates'], [])

    def test_duplicate_titles_or_conflicting_type_anchors_do_not_select_one(self):
        rows = tooltip_rows()
        for additional in (copy.deepcopy(rows[0]), row('另一装备', [1210, 300, 1410, 335]),
                           copy.deepcopy(rows[1]), row('消耗品', rows[1]['box'])):
            with self.subTest(additional=additional):
                self.assertEqual(self.read(rows + [additional])['candidates'], [])
        self.assertEqual(self.read([rows[1]])['candidates'], [])
        self.assertEqual(self.read([rows[0]])['candidates'], [])

    def test_recommendation_context_remains_separate_from_actual_equipment(self):
        for page in ('guide', 'unit_gear'):
            with self.subTest(page=page):
                result = self.read(page=page)
                self.assertEqual(result['context'], 'guide_recommendation_only')
                self.assertEqual(result['status'], 'unknown')
                self.assertIsNone(result['equipped'])
                self.assertIn('owner_selection_receipt_unbound', result['reasons'])

    def test_mismatched_ocr_source_and_non_native_images_do_not_produce_candidates(self):
        for source in (None, '', 'f' * 64):
            with self.subTest(source=source):
                result = self.read(rows_snapshot_id=source)
                self.assertEqual(result['candidates'], [])
                self.assertIn('ocr_snapshot_missing_or_mismatch', result['reasons'])
        for name, size in (('not-native.jpg', (1920, 1080)), ('small.png', (1280, 720))):
            with self.subTest(name=name):
                path = self.path.with_name(name)
                write_image(path, size=size)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                result = StateReader.read_tooltips(path, rows=tooltip_rows(), page='unknown', rows_snapshot_id=digest)
                self.assertEqual(result['candidates'], [])
                self.assertIn('unsupported_tooltip_image', result['reasons'])


class EquippedTooltipScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cw-equipment-scope-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'synthetic.png'
        write_image(self.path)

    def reader(self, rows=None):
        reader = Perception()
        reader.engine = Mock(return_value=(raw_ocr(tooltip_rows() if rows is None else rows), None))
        reader.shop_reader = Mock(read=Mock(side_effect=AssertionError('unrelated shop read')))
        reader.state_reader = Mock(read=Mock(side_effect=AssertionError('unrelated roster/inventory read')))
        return reader

    def test_unknown_and_shop_pages_use_only_primary_ocr_and_mark_unread_fields(self):
        shop_rows = [row('收起', [1580, 965, 1680, 1005]), row('刷新', [1570, 480, 1670, 520])]
        for labels, page in (([], 'unknown'), (shop_rows, 'shop')):
            reader = self.reader(tooltip_rows() + labels)
            with self.subTest(page=page), patch('currency_wars_perception.semantic_facts',
                    side_effect=AssertionError('unrelated semantic/ROI read')):
                result = reader.read(self.path, scope='equipment_tooltip')
            self.assertEqual(result['page'], page)
            self.assertEqual(result['read_contract']['effective_scope'], 'equipment_tooltip')
            self.assertEqual(result['read_contract']['version'], READ_CONTRACT_VERSION)
            self.assertEqual(READ_CONTRACT_VERSION, 4)
            self.assertEqual(reader.engine.call_count, 1)
            self.assertEqual(reader.engine.call_args.kwargs, {'use_cls': False})
            self.assertEqual(len(result['read_timing']['ocr_intervals']), 1)
            self.assertEqual(len(result['semantic']['native_tooltips']['candidates']), 1)
            for field in ('team', 'inventory', 'player_hud', 'refresh_offer', 'gear', 'rewards'):
                self.assertEqual(result['semantic'][field]['status'], 'not_read')
            self.assertEqual(result['state_read']['status'], 'not_read')
            self.assertIsNone(result['fields']['deployed'])
            reader.shop_reader.read.assert_not_called()
            reader.state_reader.read.assert_not_called()

    def test_full_unknown_and_recommendation_reads_attach_candidates_without_changing_gear(self):
        recommendation = [row('示例角色', [1510, 217, 1720, 249]),
            row('攻略推荐', [1000, 570, 1150, 600]), row('取消', [1275, 570, 1350, 601]),
            row('装备推荐', [1430, 798, 1560, 833]), row('装备追踪中', [1690, 115, 1810, 145])]
        for labels, page in (([], 'unknown'), (recommendation, 'unit_gear')):
            reader = self.reader(tooltip_rows() + labels)
            with self.subTest(page=page):
                result = reader.read(self.path)
                self.assertEqual(result['page'], page)
                self.assertEqual(len(result['semantic']['native_tooltips']['candidates']), 1)
                self.assertEqual(reader.engine.call_count, 1)
                if page == 'unit_gear':
                    gear = result['semantic']['gear']
                    self.assertEqual(gear['scope'], 'guide_recommendation_only')
                    self.assertIsNone(gear['equipped'])
                    self.assertFalse(gear['inventory_checked'])
                    self.assertEqual(result['semantic']['native_tooltips']['context'], 'guide_recommendation_only')

    def test_scope_cache_primary_reuse_force_and_new_png_have_distinct_read_counts(self):
        reader = self.reader()
        first = reader.read(self.path, scope='equipment_tooltip')
        cached = reader.read(self.path, scope='equipment_tooltip')
        full = reader.read(self.path, scope='full', reuse_primary=True)
        scoped = reader.read(self.path, scope='equipment_tooltip', reuse_primary=True)
        self.assertTrue(cached['read_timing']['cache_hit'])
        self.assertTrue(full['read_timing']['primary_ocr_reused'])
        self.assertTrue(scoped['read_timing']['primary_ocr_reused'])
        self.assertEqual(reader.engine.call_count, 1)
        self.assertEqual(scoped['read_timing']['ocr_intervals'], [])
        forced = reader.read(self.path, scope='equipment_tooltip', force=True, reuse_primary=True)
        self.assertFalse(forced['read_timing']['primary_ocr_reused'])
        self.assertEqual(reader.engine.call_count, 2)
        write_image(self.path, color=(91, 90, 90))
        fresh = reader.read(self.path, scope='equipment_tooltip', reuse_primary=True)
        self.assertNotEqual(first['snapshot_id'], fresh['snapshot_id'])
        self.assertEqual(reader.engine.call_count, 3)
        self.assertFalse(fresh['read_timing']['primary_ocr_reused'])

    def test_engine_and_ocr_contract_change_invalidate_primary_reuse(self):
        reader = self.reader()
        reader.read(self.path, scope='equipment_tooltip')
        reader.engine.text_score = .93
        changed_contract = reader.read(self.path, scope='equipment_tooltip', reuse_primary=True)
        self.assertFalse(changed_contract['read_timing']['primary_ocr_reused'])
        self.assertEqual(reader.engine.call_count, 2)
        reader.engine = Mock(return_value=(raw_ocr(tooltip_rows()), None))
        changed_engine = reader.read(self.path, scope='equipment_tooltip', reuse_primary=True)
        self.assertFalse(changed_engine['read_timing']['primary_ocr_reused'])
        self.assertEqual(reader.engine.call_count, 1)

    def test_primary_raw_score_is_not_rounded_up_into_candidate_evidence(self):
        rows = tooltip_rows()
        rows[0]['confidence'] = .89996
        reader = self.reader(rows)
        result = reader.read(self.path, scope='equipment_tooltip')
        self.assertEqual(result['rows'][0]['confidence'], .9)
        self.assertEqual(result['semantic']['native_tooltips']['candidates'], [])
        preparation = [row('备战阶段', [420, 25, 530, 60]), row('出战', [1770, 720, 1860, 780]),
            row('商店', [1585, 960, 1660, 1005]), row('3-3', [430, 65, 500, 95]),
            row('8/8', [890, 215, 1015, 275]), row('100', [1430, 65, 1485, 95])]
        reader = self.reader(rows + preparation)
        reader.state_reader = StateReader()
        reader.state_reader._load = Mock(return_value=True)
        reader.state_reader._match = Mock(return_value=[])
        result = reader.read(self.path, scope='full')
        self.assertEqual(result['page'], 'preparation')
        self.assertEqual(result['semantic']['native_tooltips']['candidates'], [])
        self.assertEqual(result['state_read']['tooltips'], [])
        self.assertEqual(reader.engine.call_count, 1)

    def test_scope_has_no_coordinate_target_or_deployment_selection_override(self):
        reader = self.reader()
        with self.assertRaises(ValueError):
            reader.read(self.path, scope='equipment_tooltip', deployment_slots=[{'row': 'front', 'slot': 1}])
        with self.assertRaises(TypeError):
            reader.read(self.path, scope='equipment_tooltip', target=[1645, 816])
        reader.engine.assert_not_called()


if __name__ == '__main__':
    unittest.main()
