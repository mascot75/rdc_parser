"""Semantic regressions for real-capture failures; no RenderDoc installation needed."""
import argparse
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from scripts.analyzer import analyze, face_estimate, write_drawcalls_csv, load_drawcalls_csv
from scripts.classify_step import write_classified_csv, load_classified_csv
from scripts.gpu_timing import TimingData, load_timing_json, merge_timing, find_qrenderdoc, load_replay_result
from scripts.reporter import write_report_md, summarise_by
from scripts.html_report import write_html_report
from scripts.xml_helpers import parse_vk_pipeline_states
from scripts.replay_export_ui import primitive_faces
from scripts.batch import run_batch


class Regressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def element(self, count=6, instances=1):
        return ET.fromstring(f'<chunk><uint name="indexCount">{count}</uint>'
                             f'<uint name="instanceCount">{instances}</uint></chunk>')

    def test_vulkan_triangle_list(self):
        self.assertEqual(face_estimate('vkCmdDrawIndexed', self.element(),
                         'VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST')[:2], ('2', True))

    def test_incomplete_primitive_per_instance(self):
        self.assertEqual(face_estimate('vkCmdDrawIndexed', self.element(8, 2),
                         'VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST')[0], '4')

    def test_zero_instances_is_known_zero(self):
        self.assertEqual(face_estimate('vkCmdDrawIndexed', self.element(6, 0),
                         'VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST')[:2], ('0', True))

    def test_missing_parameter_is_not_zero(self):
        self.assertFalse(face_estimate('vkCmdDrawIndexed', ET.fromstring('<chunk/>'),
                         'VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST')[1])

    def test_zero_indices_is_known_zero(self):
        self.assertEqual(face_estimate('vkCmdDrawIndexed', self.element(0),
                         'VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST')[:2], ('0', True))

    def test_unknown_topology_not_assumed_triangles(self):
        self.assertFalse(face_estimate('vkCmdDrawIndexed', self.element(), '')[1])

    def test_undecoded_indirect_remains_unknown(self):
        self.assertFalse(face_estimate('vkCmdDrawIndexedIndirect', self.element(),
                         'VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST')[1])

    def test_replay_primitive_count_and_restart(self):
        self.assertEqual(primitive_faces('Topology.TriangleList', 8, 2), 4)
        self.assertIsNone(primitive_faces('Topology.TriangleStrip', 8, 2, True))

    def test_vk_uint_booleans(self):
        elem = ET.fromstring('''<chunk><ResourceId name="Pipeline">42</ResourceId>
          <struct name="CreateInfo"><struct name="pColorBlendState">
          <uint name="blendEnable">1</uint></struct><struct name="pDepthStencilState">
          <uint name="depthTestEnable">1</uint><uint name="depthWriteEnable">1</uint>
          </struct></struct></chunk>''')
        state = parse_vk_pipeline_states(elem)['42']
        self.assertTrue(state['blend'].enabled)
        self.assertTrue(state['depth'].depth_enable)
        self.assertEqual(state['depth'].write_mask, 'ALL')

    def record(self):
        xml = self.root / 'sample.xml'
        xml.write_text('''<rdc><chunks><chunk name="vkCmdDrawIndexed" chunkIndex="4137">
          <ResourceId name="commandBuffer">1</ResourceId><uint name="indexCount">6</uint>
          <uint name="instanceCount">1</uint></chunk></chunks></rdc>''')
        return analyze(xml)[0]

    def test_no_capacity_fallback(self):
        row = self.record()
        self.assertFalse(row.face_known)
        self.assertEqual(row.combined_faces_estimate, '')

    def test_event_namespace_csv_roundtrip(self):
        row = self.record()
        row.event, row.event_space, row.chunk_index = 17, 'replay_event', 4137
        for writer, reader in [(write_drawcalls_csv, load_drawcalls_csv),
                               (write_classified_csv, load_classified_csv)]:
            path = self.root / 'records.csv'
            writer(path, [row])
            loaded = reader(path)[0]
            self.assertEqual((loaded.event, loaded.event_space, loaded.chunk_index), (17, 'replay_event', 4137))

    def test_reject_wrong_timing_namespace(self):
        with self.assertRaises(ValueError):
            merge_timing([self.record()], TimingData({4137: 1.0}, key_space='replay_event'))

    def test_missing_timing_is_blank_not_zero(self):
        row = self.record()
        second = self.record()
        second.event = 22
        values = merge_timing([row, second], TimingData({4137: 0.0}))
        self.assertEqual(values[0]['gpu_time_ms'], 0.0)
        self.assertEqual(values[1]['gpu_time_ms'], '')
        self.assertFalse(values[1]['timing_available'])

    def test_draw_percentage_excludes_other_events(self):
        row = self.record()
        summary = summarise_by([row], 'pass_name', {4137: 2.0, 9000: 8.0})
        self.assertEqual(summary[0]['time_pct'], 100.0)

    def test_empty_reports_fail(self):
        for writer in (write_report_md, write_html_report):
            with self.assertRaises(ValueError):
                writer(self.root / 'report', [])

    def test_timing_rejects_nonfinite_negative_values(self):
        path = self.root / 'timing.json'
        path.write_text(json.dumps({'key_space': 'replay_event', 'timing': {'1': 2, '2': float('nan'), '3': -1}}))
        timing = load_timing_json(path)
        self.assertEqual(timing, {1: 2.0})
        self.assertEqual(timing.key_space, 'replay_event')

    def test_renderdoccmd_resolves_sibling_qrenderdoc(self):
        (self.root / 'renderdoccmd.exe').touch()
        (self.root / 'qrenderdoc.exe').touch()
        self.assertEqual(find_qrenderdoc(str(self.root / 'renderdoccmd.exe')), str(self.root / 'qrenderdoc.exe'))

    def test_batch_failure_is_not_success(self):
        (self.root / 'a.rdc').touch()
        with self.assertRaises(ValueError):
            run_batch(self.root, self.root / 'output', lambda *_: (_ for _ in ()).throw(ValueError('failed replay')))

    def test_cached_replay_rejects_changed_capture(self):
        capture, cached = self.root / 'a.rdc', self.root / 'data.json'
        capture.write_bytes(b'capture')
        stat = capture.stat()
        cached.write_text(json.dumps({'schema_version': 2, 'status': 'ok', 'drawcalls': [{}],
            'source': {'path': str(capture), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}}))
        self.assertEqual(load_replay_result(cached, capture)['status'], 'ok')
        capture.write_bytes(b'different capture')
        with self.assertRaises(ValueError):
            load_replay_result(cached, capture)

    def test_cached_replay_rejects_other_capture(self):
        capture, other, cached = self.root / 'a.rdc', self.root / 'b.rdc', self.root / 'data.json'
        capture.write_bytes(b'capture')
        other.write_bytes(b'capture')
        stat = other.stat()
        cached.write_text(json.dumps({'schema_version': 2, 'status': 'ok', 'drawcalls': [{}],
            'source': {'path': str(capture), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}}))
        with self.assertRaises(ValueError):
            load_replay_result(cached, other)


if __name__ == '__main__':
    unittest.main()
