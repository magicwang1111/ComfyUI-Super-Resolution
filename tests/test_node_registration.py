import importlib.util
import sys
import unittest
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "comfyui_super_resolution",
    PACKAGE_DIR / "__init__.py",
    submodule_search_locations=[str(PACKAGE_DIR)],
)
PACKAGE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PACKAGE
SPEC.loader.exec_module(PACKAGE)


class NodeRegistrationTests(unittest.TestCase):
    def test_registers_las_and_mediakit_as_separate_nodes(self):
        las_class = PACKAGE.NODE_CLASS_MAPPINGS["LASVideoSuperResolution"]
        mediakit_class = PACKAGE.NODE_CLASS_MAPPINGS["VolcengineVideoEnhance"]

        self.assertEqual(las_class.__name__, "LASVideoSuperResolution")
        self.assertEqual(las_class.CATEGORY, "Volcengine/LAS")
        self.assertEqual(mediakit_class.__name__, "VolcengineVideoEnhance")
        self.assertEqual(mediakit_class.CATEGORY, "Volcengine/AI MediaKit")
        self.assertIsNot(las_class, mediakit_class)

    def test_registers_tencent_as_an_independent_node(self):
        node = PACKAGE.NODE_CLASS_MAPPINGS["TencentMPSVideoEnhance"]
        self.assertEqual(node.CATEGORY, "Tencent/MPS")
        self.assertEqual(node.RETURN_NAMES, ("local_video_path", "cos_video_url", "task_id"))
        self.assertEqual(len(PACKAGE.NODE_CLASS_MAPPINGS), 3)


if __name__ == "__main__":
    unittest.main()
