import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "volcengine_video_enhance.py"
SPEC = importlib.util.spec_from_file_location("volcengine_video_enhance", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class VideoEnhanceTests(unittest.TestCase):
    def test_registers_new_node_without_claiming_legacy_id(self):
        self.assertIs(
            MODULE.NODE_CLASS_MAPPINGS["VolcengineVideoEnhance"],
            MODULE.VolcengineVideoEnhance,
        )
        self.assertNotIn("LASVideoSuperResolution", MODULE.NODE_CLASS_MAPPINGS)

    def test_keeps_legacy_widget_slots_for_saved_workflow_migration(self):
        optional = MODULE.VolcengineVideoEnhance.INPUT_TYPES()["optional"]
        keys = list(optional)
        self.assertLess(keys.index("preserve_audio"), keys.index("tool_version"))
        self.assertLess(keys.index("output_quality_mode"), keys.index("tool_version"))

    def test_builds_standard_payload_and_maps_legacy_resolution(self):
        payload = MODULE.VolcengineVideoEnhance._build_payload(
            "https://example.com/input.mp4",
            "2160p",
            "standard",
            "aigc",
            "high",
            60,
            "auto",
        )
        self.assertEqual(payload["resolution"], "4k")
        self.assertEqual(payload["scene"], "aigc")
        self.assertEqual(payload["fps"], 60)
        self.assertNotIn("bit_depth", payload)

    def test_builds_professional_payload_without_scene(self):
        payload = MODULE.VolcengineVideoEnhance._build_payload(
            "https://example.com/input.mp4",
            "4k",
            "professional",
            "old_film",
            "medium",
            0,
            "10",
        )
        self.assertNotIn("scene", payload)
        self.assertNotIn("fps", payload)
        self.assertEqual(payload["bit_depth"], 10)

    def test_rejects_bit_depth_in_standard_mode(self):
        with self.assertRaisesRegex(ValueError, "professional"):
            MODULE.VolcengineVideoEnhance._build_payload(
                "https://example.com/input.mp4",
                "1080p",
                "standard",
                "common",
                "medium",
                0,
                "10",
            )

    def test_local_input_uploads_and_signs_oss_url(self):
        config = {
            "oss_prefix": "input",
            "oss_signed_url_expires": 86400,
        }
        bucket = Mock()
        bucket.sign_url.return_value = "https://signed.example/input.mp4"
        with patch.object(MODULE, "oss_bucket", return_value=bucket):
            with self.subTest("upload"):
                source = Path(__file__)
                url = MODULE.upload_local_input(source, config)
        bucket.put_object_from_file.assert_called_once()
        bucket.sign_url.assert_called_once()
        self.assertEqual(url, "https://signed.example/input.mp4")

    def test_completed_task_returns_result(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "success": True,
            "status": "completed",
            "result": {"video_url": "https://example.com/output.mp4"},
        }
        config = {"poll_interval_seconds": 1, "poll_timeout_seconds": 1}
        with patch.object(MODULE.requests, "get", return_value=response):
            result = MODULE.VolcengineVideoEnhance._wait_for_completion(
                "https://mediakit.example",
                {"Authorization": "Bearer redacted"},
                "task-1",
                config,
            )
        self.assertEqual(result["video_url"], "https://example.com/output.mp4")


if __name__ == "__main__":
    unittest.main()
