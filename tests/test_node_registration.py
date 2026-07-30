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


if __name__ == "__main__":
    unittest.main()
