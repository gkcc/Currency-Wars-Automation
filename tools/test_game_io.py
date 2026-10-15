"""Cross-platform, input-free contract checks for the coaching game tool."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


TOOL = Path(__file__).with_name("game_io.py")
spec = importlib.util.spec_from_file_location("game_io_test_target", TOOL)
game_io = importlib.util.module_from_spec(spec)
spec.loader.exec_module(game_io)


class GameIOContractTests(unittest.TestCase):
    def test_preview_scaling_and_boundary(self):
        self.assertEqual(game_io.map_point(0, 0, (100, 200, 2020, 1280)), (100, 200))
        self.assertEqual(game_io.map_point(960, 540, (100, 200, 2020, 1280)), (1060, 740))
        with self.assertRaises(game_io.Rejected):
            game_io.map_point(1920, 500, (0, 0, 1920, 1080))
        with self.assertRaises(game_io.Rejected):
            game_io.map_point(300, -1, (0, 0, 1920, 1080))

    def test_inspect_requires_snapshot_reference(self):
        with patch.object(sys, "argv", ["game_io.py", "inspect", "450", "320"]):
            with self.assertRaises(SystemExit):
                game_io.arguments()
        with patch.object(sys, "argv", ["game_io.py", "inspect", "450", "320", "--frame", "abc"]):
            args = game_io.arguments()
            self.assertEqual((args.action, args.frame, args.x, args.y),
                             ("inspect", "abc", 450, 320))

    def test_stop_and_resume_are_not_game_actions(self):
        with patch.object(sys, "argv", ["game_io.py", "stop"]):
            self.assertEqual(game_io.arguments().action, "stop")
        with patch.object(sys, "argv", ["game_io.py", "resume"]):
            self.assertEqual(game_io.arguments().action, "resume")


if __name__ == "__main__":
    unittest.main()
