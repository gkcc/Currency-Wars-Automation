"""Deployment source closure only; no GUI, private resources or old test run."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import currency_wars_source_guard as guard
import test_currency_wars_source_runtime as source_fixture


DEPLOYMENT_SOURCE = 'tools/currency_wars_deployment.py'


class DeploymentRuntimeTests(unittest.TestCase):
    def test_real_deployment_import_requires_the_shared_inventory_entry(self):
        with tempfile.TemporaryDirectory(prefix='currency-wars-deployment-sources-') as temporary:
            fixture = source_fixture.SourceRuntimeTests()
            fixture.root = Path(temporary)
            project = fixture.source_copy()
            files = guard.production_files(project)
            self.assertEqual(len(files), 25)
            self.assertIn(DEPLOYMENT_SOURCE, files)
            self.assertIn(guard.SOURCE_MANIFEST, files)
            manifest_path = project / guard.SOURCE_MANIFEST
            payload = manifest_path.read_bytes()
            manifest = json.loads(payload)
            manifest['files'].remove(DEPLOYMENT_SOURCE)
            try:
                manifest_path.write_text(json.dumps(manifest), encoding='utf8')
                with self.assertRaisesRegex(ValueError, 'absent from the source inventory'):
                    guard.production_files(project)
            finally:
                manifest_path.write_bytes(payload)
            (project / DEPLOYMENT_SOURCE).unlink()
            with self.assertRaisesRegex(ValueError, 'Production dependency is missing'):
                guard.production_files(project)

    def test_runtime_binding_refuses_changed_or_unreviewed_deployment_bytes(self):
        with tempfile.TemporaryDirectory(prefix='currency-wars-deployment-byte-gate-') as temporary:
            fixture = source_fixture.SourceRuntimeTests()
            fixture.root = Path(temporary)
            project = fixture.source_copy()
            # Existing helper makes inert resource bytes and reuses public assets.
            # This is a source-binding protocol, never a real READY approval.
            binding = fixture.resource_fixture(project)
            reviewed = guard.source_hashes(project)
            self.assertEqual(guard.verify_runtime_sources(project, binding), binding)
            self.assertNotIn('ready', binding)
            missing = copy.deepcopy(binding)
            missing['hashes'].pop(DEPLOYMENT_SOURCE)
            with self.assertRaisesRegex(ValueError, 'source set is missing or changed'):
                guard.verify_runtime_sources(project, missing)
            for name in (DEPLOYMENT_SOURCE, guard.SOURCE_MANIFEST):
                path = project / name
                payload = path.read_bytes()
                with self.subTest(source=name):
                    try:
                        path.write_bytes(payload + b'\n')
                        with self.assertRaisesRegex(ValueError, 'changed or was not reviewed'):
                            guard.verify_source_hashes(project, reviewed)
                        with self.assertRaisesRegex(ValueError, 'source bytes changed'):
                            guard.verify_runtime_sources(project, binding)
                    finally:
                        path.write_bytes(payload)
            self.assertEqual(guard.verify_runtime_sources(project, binding), binding)


if __name__ == '__main__':
    unittest.main()
