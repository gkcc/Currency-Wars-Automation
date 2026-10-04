"""Offline installation preflight. Does not import a controller or open a GUI."""
from importlib.metadata import PackageNotFoundError, version
import argparse
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parent.parent
PACKAGES = ('Pillow', 'numpy', 'opencv-python', 'rapidocr-onnxruntime', 'psutil')


def check(project=PROJECT):
    dependencies = {}
    for package in PACKAGES:
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = None
    gui = project / 'gui/bin/currency-wars-gui.exe'
    resources = project / 'tools/shop_reader_resources'
    ready = project / 'docs/RUNNER_READY.json'
    return dict(python_supported=(3, 11) <= sys.version_info[:2] <= (3, 13),
                dependencies=dependencies, gui_binary_present=gui.is_file(),
                shop_resources_present=(resources / 'SOURCES.json').is_file() and (resources / 'names.json').is_file(),
                readiness_record_present=ready.is_file(),
                note='Resource/readiness presence does not prove validity; GUI verifies source hashes and session ownership.',
                game_inputs=0, controllers_started=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-launch', action='store_true')
    args = parser.parse_args()
    result = check()
    if args.check_launch:
        from currency_wars_update import launch_prerequisites
        result['launch_blocker'] = launch_prerequisites(PROJECT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['python_supported'] and all(result['dependencies'].values()) and result['gui_binary_present'] and not result.get('launch_blocker') else 1)
