import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qcloud_cos.streambody import StreamBody
from requests import Response
from urllib3.response import HTTPResponse
from urllib3.exceptions import ProtocolError


SPEC = importlib.util.spec_from_file_location(
    "tencent_mps_video_enhance", Path(__file__).resolve().parents[1] / "tencent_mps_video_enhance.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def config():
    return {
        "tencent_secret_id": "test-id", "tencent_secret_key": "test-secret",
        "tencent_region": "ap-guangzhou", "tencent_cos_bucket": "test-123",
        "tencent_request_timeout": 600, "tencent_poll_interval": 5,
        "tencent_max_wait_seconds": 3600,
    }


def result(status="SUCCESS", **kwargs):
    task = {"Status": status, "ErrCode": 0, "ErrCodeExt": "", "Input": {"Definition": 327003},
            "Output": {"Path": "/output/video.mp4"}}
    task.update(kwargs)
    return {"Status": "FINISH" if status != "PROCESSING" else "PROCESSING", "WorkflowTask": {
        "ErrCode": 0, "MediaProcessResultSet": [{"Type": "Transcode", "TranscodeTask": task}],
    }}


def response(data):
    return SimpleNamespace(to_json_string=lambda: json.dumps(data))


def download_response(data=b"video", expected=5, **headers):
    rt = Response()
    rt.status_code = 200
    rt.headers.update({"Content-Length": str(expected), **headers})
    rt.raw = HTTPResponse(body=io.BytesIO(data), preload_content=False)
    return {**rt.headers, "Body": StreamBody(rt)}


class TencentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output_directory = Path(directory.name)
        paths = patch.object(MODULE, "folder_paths", SimpleNamespace(get_output_directory=lambda: directory.name))
        paths.start()
        self.addCleanup(paths.stop)

    def test_all_official_template_pairs_and_sdk_serialization(self):
        expected = {
            "真人": [327001, 327003, 327005, 327007], "漫剧": [327002, 327004, 327006, 327008],
            "老片/低清修复": [327021, 327022, 327023, 327024],
            "真人小脸优化": [327025, 327026, 327027, 327028],
            "漫剧小脸优化": [327029, 327030, 327031, 327032],
            "Seedance2": [327033, 327034, 327035, 327036],
        }
        for scene, ids in expected.items():
            for resolution, definition in zip(["720p", "1080p", "2k", "4k"], ids):
                with self.subTest(scene=scene, resolution=resolution):
                    payload = MODULE.build_payload({"Type": "URL", "UrlInputInfo": {"Url": "https://example.com/in.mp4"}},
                                                   scene, resolution, config(), "run-1", "../../output")
                    request = MODULE.models.ProcessMediaRequest()
                    request.from_json_string(json.dumps(payload))
                    task = json.loads(request.to_json_string())["MediaProcessTask"]["TranscodeTaskSet"][0]
                    self.assertEqual(task["Definition"], definition)
                    self.assertIsNone(task.get("OverrideParameter"))
                    self.assertNotIn("..", task["OutputObjectPath"])
                    self.assertEqual(payload["SessionId"], "run-1")

    def test_invalid_preset_fails_before_upload(self):
        with patch.object(MODULE, "load_config", return_value=config()), patch.object(MODULE, "create_clients") as clients:
            with self.assertRaises(ValueError):
                MODULE.TencentMPSVideoEnhance().upscale("", "真人", "8k")
            clients.assert_not_called()

    def test_url_has_priority_and_does_not_upload(self):
        cos = Mock()
        value = MODULE.resolve_input("https://example.com/in.mp4", "missing.mp4", config(), cos)
        self.assertEqual(value["Type"], "URL")
        cos.upload_file.assert_not_called()

    def test_local_path_and_selected_file_upload_to_cos(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test.mp4"
            source.write_bytes(b"video")
            paths = SimpleNamespace(get_input_directory=lambda: directory)
            with patch.object(MODULE, "folder_paths", paths):
                for video_url, local in [(str(source), "missing.mp4"), ("", "test.mp4")]:
                    with self.subTest(video_url=video_url):
                        cos = Mock()
                        value = MODULE.resolve_input(video_url, local, config(), cos)
                        self.assertEqual(value["Type"], "COS")
                        self.assertEqual(value["CosInputInfo"]["Bucket"], "test-123")
                        cos.upload_file.assert_called_once()
                        self.assertTrue(value["CosInputInfo"]["Object"].startswith("/mps-super-resolution/input/"))

    def test_selected_path_cannot_escape_input_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(MODULE, "folder_paths", SimpleNamespace(get_input_directory=lambda: directory)):
            with self.assertRaisesRegex(ValueError, "inside"):
                MODULE.resolve_input("", "../outside.mp4", config(), Mock())

    def test_config_errors_do_not_disclose_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch.object(MODULE, "LOCAL_CONFIG_PATH", path):
                with self.assertRaisesRegex(RuntimeError, "Copy"):
                    MODULE.load_config()
                for data in ["[]", '{"secret":"private",}', json.dumps({**config(), "tencent_secret_id": ""})]:
                    path.write_text(data, encoding="utf-8")
                    with self.assertRaises((ValueError, RuntimeError)) as error:
                        MODULE.load_config()
                    self.assertNotIn("private", str(error.exception))
                    self.assertNotIn("test-secret", str(error.exception))
                for invalid in [0, -1, float("inf")]:
                    path.write_text(json.dumps({**config(), "tencent_poll_interval": invalid}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        MODULE.load_config()
                path.write_text(json.dumps(config()), encoding="utf-8")
                self.assertEqual(MODULE.load_config()["tencent_poll_interval"], 5)

    def test_missing_sdk_gives_install_hint(self):
        with patch.object(MODULE, "mps_client", None):
            with self.assertRaisesRegex(RuntimeError, "pip install"):
                MODULE.create_clients(config())

    def test_clients_use_https_timeout_and_no_submission_retries(self):
        client, cos = MODULE.create_clients(config())
        self.assertEqual(client.request.conn.timeout, 600)
        self.assertEqual(client.profile.httpProfile.endpoint, "mps.tencentcloudapi.com")
        self.assertIsNone(client.profile.retryer)
        self.assertEqual(cos._conf._scheme, "https")

    def test_processing_then_success_and_transport_timeout_bound(self):
        client, _ = MODULE.create_clients(config())
        client.DescribeTaskDetail = Mock(side_effect=[response(result("PROCESSING", Progress=50)), response(result())])
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        progress = Mock()
        with patch.object(MODULE.time, "monotonic", side_effect=lambda: now[0]), patch.object(MODULE.time, "sleep", side_effect=sleep):
            output = MODULE.wait_for_completion(client, "task-1", 327003, {**config(), "tencent_max_wait_seconds": 10}, progress)
        self.assertEqual(output["Path"], "/output/video.mp4")
        self.assertEqual(client.DescribeTaskDetail.call_count, 2)
        self.assertEqual(client.request.conn.timeout, 5)
        progress.update_absolute.assert_called_with(55)

    def test_workflow_and_subtask_failures_and_missing_results(self):
        cases = [
            {"Status": "FINISH", "WorkflowTask": {"ErrCode": 100}},
            result("FAIL", ErrCode=1), result("SUCCESS", ErrCodeExt="FailedOperation"),
            result(Output=None), result(Output={}),
            {"Status": "FINISH", "WorkflowTask": {"ErrCode": 0, "MediaProcessResultSet": []}},
            {"Status": "FINISH", "WorkflowTask": {"MediaProcessResultSet": [{"Type": "Transcode", "TranscodeTask": {"Input": None}}]}},
        ]
        for case in cases:
            with self.subTest(case=case):
                client = Mock()
                client.DescribeTaskDetail.return_value = response(case)
                with self.assertRaisesRegex(RuntimeError, "task-1"):
                    MODULE.wait_for_completion(client, "task-1", 327003, config(), None)
                client.ProcessMedia.assert_not_called()

    def test_timeout_does_not_resubmit(self):
        client = Mock()
        client.DescribeTaskDetail.return_value = response(result("PROCESSING"))
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        with patch.object(MODULE.time, "monotonic", side_effect=lambda: now[0]), patch.object(MODULE.time, "sleep", side_effect=sleep):
            with self.assertRaisesRegex(TimeoutError, "task-1"):
                MODULE.wait_for_completion(client, "task-1", 327003, {**config(), "tencent_max_wait_seconds": 1}, None)
        client.ProcessMedia.assert_not_called()

    def test_interrupt_during_poll_wait(self):
        client = Mock()
        client.DescribeTaskDetail.return_value = response(result("PROCESSING"))
        with patch.object(MODULE, "throw_exception_if_processing_interrupted", side_effect=[None, InterruptedError("cancel")]):
            with self.assertRaises(InterruptedError):
                MODULE.wait_for_completion(client, "task-1", 327003, config(), None)
        client.DescribeTaskDetail.assert_called_once()
        client.ProcessMedia.assert_not_called()

    def test_query_error_includes_task_without_secret(self):
        client = Mock()
        client.DescribeTaskDetail.side_effect = MODULE.TencentCloudSDKException("AuthFailure", "test-secret")
        with self.assertRaisesRegex(RuntimeError, "task-1") as error:
            MODULE.wait_for_completion(client, "task-1", 327003, config(), None)
        self.assertNotIn("test-secret", str(error.exception))

    def test_private_cos_download_and_atomic_output(self):
        with tempfile.TemporaryDirectory() as directory:
            cos = Mock()
            cos.get_object.return_value = download_response(ETag='"' + hashlib.md5(b"video").hexdigest() + '"')
            cos.get_presigned_url.return_value = "https://example.com/signed.mp4"
            with patch.object(MODULE, "folder_paths", SimpleNamespace(get_output_directory=lambda: directory)):
                path, url, task = MODULE.download_result(cos, {"Path": "/output/video.mp4"}, config(), "task-1", "../../name")
            self.assertEqual(Path(path).read_bytes(), b"video")
            self.assertTrue(Path(path).is_relative_to(Path(directory)))
            self.assertFalse(list(Path(directory).rglob("*.part")))
            self.assertEqual(task, "task-1")
            self.assertEqual(url, "https://example.com/signed.mp4")
            cos.get_object.assert_called_once_with(Bucket="test-123", Key="output/video.mp4")
            self.assertEqual(cos.get_presigned_url.call_args.kwargs["Expired"], 86400)

    def test_incomplete_and_interrupted_downloads_leave_no_final_file(self):
        for interrupt in [False, True]:
            with self.subTest(interrupt=interrupt), tempfile.TemporaryDirectory() as directory:
                cos = Mock()
                cos.get_object.side_effect = lambda **kwargs: download_response(expected=50)
                with patch.object(MODULE, "folder_paths", SimpleNamespace(get_output_directory=lambda: directory)), patch.object(
                    MODULE, "throw_exception_if_processing_interrupted", side_effect=[None, InterruptedError()] if interrupt else None
                ):
                    with self.assertRaises((RuntimeError, InterruptedError)):
                        MODULE.download_result(cos, {"Path": "/output/video.mp4"}, config(), "task-1", "")
                self.assertFalse(list(Path(directory).rglob("*.mp4")))
                self.assertFalse(list(Path(directory).rglob("*.part")))
                self.assertTrue(list(Path(directory).rglob("task-1.json")))
                self.assertEqual(cos.get_object.call_count, 1 if interrupt else 3)

    def test_empty_truncated_and_checksum_failures_retry_then_succeed(self):
        corruptions = [download_response(b""), download_response(b"vid"),
                       download_response(b"other", ETag='"' + hashlib.md5(b"video").hexdigest() + '"')]
        for first in corruptions:
            with self.subTest(first=first):
                cos = Mock()
                cos.get_object.side_effect = [first, download_response()]
                path, _, _ = MODULE.download_result(cos, {"Path": "/output/video.mp4"}, config(), "task-1", "")
                self.assertEqual(Path(path).read_bytes(), b"video")
                self.assertEqual(cos.get_object.call_count, 2)
                self.assertTrue(first["Body"].get_raw_stream().closed)
                self.assertFalse(list(self.output_directory.rglob("*.part")))

    def test_connection_stream_and_server_errors_retry(self):
        for failure in [MODULE.CosClientError("test-secret"),
                        MODULE.CosServiceError("GET", {"code": "SlowDown"}, 503),
                        MODULE.CosServiceError("GET", {"code": "SlowDown"}, 429), "stream"]:
            with self.subTest(failure=failure):
                cos = Mock()
                first = failure
                if failure == "stream":
                    first = download_response()
                    first["Body"].get_raw_stream().stream = Mock(side_effect=ProtocolError("broken connection"))
                cos.get_object.side_effect = [first, download_response()]
                path, _, _ = MODULE.download_result(cos, {"Path": "/output/video.mp4"}, config(), "task-1", "")
                self.assertEqual(Path(path).read_bytes(), b"video")
                self.assertEqual(cos.get_object.call_count, 2)

    def test_permission_error_does_not_retry_or_expose_secrets(self):
        cos = Mock()
        cos.get_object.side_effect = MODULE.CosServiceError("GET", {"code": "AccessDenied", "message": "test-secret"}, 403)
        with self.assertRaisesRegex(RuntimeError, "COS HTTP 403") as error:
            MODULE.download_result(cos, {"Path": "/output/video.mp4"}, config(), "task-1", "")
        self.assertIn("resume_task_id=task-1", str(error.exception))
        self.assertNotIn("test-secret", str(error.exception))
        cos.get_object.assert_called_once()

    def test_cancel_during_retry_wait_stops_download(self):
        cos = Mock()
        cos.get_object.side_effect = lambda **kwargs: download_response(b"")
        with patch.object(MODULE.time, "sleep", side_effect=InterruptedError("cancel")):
            with self.assertRaises(InterruptedError):
                MODULE.download_result(cos, {"Path": "/output/video.mp4"}, config(), "task-1", "")
        cos.get_object.assert_called_once()
        self.assertFalse(list(self.output_directory.rglob("*.mp4")))
        self.assertFalse(list(self.output_directory.rglob("*.part")))

    def test_lowercase_headers_still_check_size_and_multipart_etag_is_not_md5(self):
        cos = Mock()
        first = download_response(b"vid")
        first["content-length"] = first.pop("Content-Length")
        cos.get_object.side_effect = [first, download_response(ETag='"not-a-single-part-md5-2"')]
        path, _, _ = MODULE.download_result(cos, {"Path": "/output/video.mp4"}, config(), "task-1", "")
        self.assertEqual(Path(path).read_bytes(), b"video")
        self.assertEqual(cos.get_object.call_count, 2)

    def test_download_exhaustion_can_resume_after_restart_without_submission_or_query(self):
        client, cos = Mock(), Mock()
        client.ProcessMedia.return_value.TaskId = "task-1"
        client.DescribeTaskDetail.return_value = response(result())
        cos.get_object.side_effect = lambda **kwargs: download_response(b"")
        with patch.object(MODULE, "load_config", return_value=config()), patch.object(MODULE, "create_clients", return_value=(client, cos)):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempt.*received=0, expected=5.*resume_task_id=task-1"):
                MODULE.TencentMPSVideoEnhance().upscale("https://example.com/in.mp4", "真人", "1080p")
            self.assertEqual(cos.get_object.call_count, 3)
            record = MODULE.task_record_path("task-1").read_text(encoding="utf-8")
            self.assertNotIn("test-secret", record)
            self.assertIn("output/video.mp4", record)
            cos.get_object.side_effect = [download_response()]
            path, _, task_id = MODULE.TencentMPSVideoEnhance().upscale("", "漫剧", "4k", resume_task_id="task-1")
        self.assertEqual(Path(path).read_bytes(), b"video")
        self.assertEqual(task_id, "task-1")
        client.ProcessMedia.assert_called_once()
        client.DescribeTaskDetail.assert_called_once()
        cos.upload_file.assert_not_called()

    def test_resume_without_record_queries_original_task_ignoring_new_preset(self):
        client, cos = Mock(), Mock()
        client.DescribeTaskDetail.return_value = response(result())
        cos.get_object.return_value = download_response()
        with patch.object(MODULE, "load_config", return_value=config()), patch.object(MODULE, "create_clients", return_value=(client, cos)):
            path, _, task = MODULE.TencentMPSVideoEnhance().upscale("", "漫剧", "4k", resume_task_id=" task-old ")
        self.assertEqual(Path(path).read_bytes(), b"video")
        self.assertEqual(task, "task-old")
        self.assertEqual(client.DescribeTaskDetail.call_args.args[0].TaskId, "task-old")
        client.ProcessMedia.assert_not_called()
        cos.upload_file.assert_not_called()

    def test_invalid_resume_id_fails_before_network_access(self):
        with patch.object(MODULE, "load_config", return_value=config()), patch.object(MODULE, "create_clients") as clients:
            with self.assertRaisesRegex(ValueError, "resume_task_id"):
                MODULE.TencentMPSVideoEnhance().upscale("", "真人", "1080p", resume_task_id="../../outside")
        clients.assert_not_called()

    def test_resume_record_for_other_bucket_is_rejected(self):
        record = MODULE.task_record_path("task-1")
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"Path": "/output/video.mp4", "OutputStorage": {"Type": "COS", "CosOutputStorage": {
            "Bucket": "other-123", "Region": "ap-guangzhou"}}}), encoding="utf-8")
        client, cos = Mock(), Mock()
        with patch.object(MODULE, "load_config", return_value=config()), patch.object(MODULE, "create_clients", return_value=(client, cos)):
            with self.assertRaisesRegex(RuntimeError, "Unexpected MPS output storage"):
                MODULE.TencentMPSVideoEnhance().upscale("", "真人", "1080p", resume_task_id="task-1")
        client.ProcessMedia.assert_not_called()
        cos.get_object.assert_not_called()

    def test_node_orchestrates_one_submission(self):
        client = Mock()
        client.ProcessMedia.return_value.TaskId = "task-1"
        client.DescribeTaskDetail.return_value = response(result())
        with patch.object(MODULE, "load_config", return_value=config()), patch.object(MODULE, "create_clients", return_value=(client, Mock())), patch.object(
            MODULE, "download_result", return_value=("local.mp4", "https://example.com/result", "task-1")
        ):
            output = MODULE.TencentMPSVideoEnhance().upscale("https://example.com/in.mp4", "真人", "1080p")
        self.assertEqual(output[2], "task-1")
        client.ProcessMedia.assert_called_once()

    def test_submission_error_is_not_retried(self):
        client = Mock()
        client.ProcessMedia.side_effect = MODULE.TencentCloudSDKException("RequestTimeout", "test-secret")
        with patch.object(MODULE, "load_config", return_value=config()), patch.object(MODULE, "create_clients", return_value=(client, Mock())):
            with self.assertRaisesRegex(RuntimeError, "session_id") as error:
                MODULE.TencentMPSVideoEnhance().upscale("https://example.com/in.mp4", "真人", "1080p")
        self.assertNotIn("test-secret", str(error.exception))
        client.ProcessMedia.assert_called_once()
        client.DescribeTaskDetail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
