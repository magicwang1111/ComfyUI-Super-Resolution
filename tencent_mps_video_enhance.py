import hashlib
import json
import math
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from urllib3.exceptions import HTTPError as StreamHTTPError

try:
    import folder_paths
    from comfy.model_management import throw_exception_if_processing_interrupted
    from comfy.utils import ProgressBar
except ImportError:
    folder_paths = None
    ProgressBar = None

    def throw_exception_if_processing_interrupted():
        pass

try:
    from tencentcloud.common import credential
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.mps.v20190612 import models, mps_client
except ImportError:
    mps_client = None

try:
    from qcloud_cos import CosConfig, CosS3Client
    from qcloud_cos.cos_exception import CosClientError, CosServiceError
except ImportError:
    CosS3Client = None


NODE_DIR = Path(__file__).resolve().parent
LOCAL_CONFIG_PATH = NODE_DIR / "config.tencent.local.json"
RESOLUTIONS = ("720p", "1080p", "2k", "4k")
TEMPLATES = {
    "真人": (327001, 327003, 327005, 327007),
    "漫剧": (327002, 327004, 327006, 327008),
    "老片/低清修复": (327021, 327022, 327023, 327024),
    "真人小脸优化": (327025, 327026, 327027, 327028),
    "漫剧小脸优化": (327029, 327030, 327031, 327032),
    "Seedance2": (327033, 327034, 327035, 327036),
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".ts", ".wmv", ".m4v", ".mpeg", ".mpg"}


def load_config():
    try:
        config = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError("Copy config.tencent.local.example.json to config.tencent.local.json and fill in Tencent credentials.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid JSON in config.tencent.local.json") from exc
    if not isinstance(config, dict):
        raise ValueError("config.tencent.local.json must be a JSON object")
    required = ("tencent_secret_id", "tencent_secret_key", "tencent_region", "tencent_cos_bucket")
    missing = [key for key in required if not isinstance(config.get(key), str) or not config[key].strip()]
    if missing:
        raise ValueError(f"Missing config.tencent.local.json fields: {', '.join(missing)}")
    if config.get("tencent_mps_version", "2019-06-12") != "2019-06-12":
        raise ValueError("tencent_mps_version must be 2019-06-12")
    for key, default in (("tencent_request_timeout", 600), ("tencent_poll_interval", 5), ("tencent_max_wait_seconds", 3600)):
        value = float(config.get(key, default))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a positive finite number")
        config[key] = value
    return config


def create_clients(config):
    if mps_client is None or CosS3Client is None:
        raise RuntimeError("Missing Tencent SDK: run python -m pip install -r requirements.txt in the ComfyUI environment")
    profile = HttpProfile()
    profile.endpoint = config.get("tencent_mps_host", "mps.tencentcloudapi.com")
    profile.reqTimeout = config["tencent_request_timeout"]
    client_profile = ClientProfile()
    client_profile.httpProfile = profile
    # Do not retry task submissions: an ambiguous response may already have created a paid task.
    client_profile.retryer = None
    auth = credential.Credential(config["tencent_secret_id"], config["tencent_secret_key"])
    client = mps_client.MpsClient(auth, config["tencent_region"], client_profile)
    cos = CosS3Client(CosConfig(
        Region=config["tencent_region"], SecretId=config["tencent_secret_id"],
        SecretKey=config["tencent_secret_key"], Scheme="https", Timeout=config["tencent_request_timeout"],
    ))
    return client, cos


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value)).strip("._")[:100] or "video"


def object_key(prefix, filename):
    prefix = str(prefix).strip("/\\")
    return f"{prefix}/{filename}" if prefix else filename


def input_video_files():
    if folder_paths is None:
        return [""]
    return [""] + sorted(p.name for p in Path(folder_paths.get_input_directory()).iterdir()
                         if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def resolve_input(video_url, local_video, config, cos):
    source = str(video_url or "").strip()
    if source.startswith(("http://", "https://")):
        if not urlparse(source).netloc:
            raise ValueError("video_url must be a valid HTTP(S) video URL")
        return {"Type": "URL", "UrlInputInfo": {"Url": source}}
    if source:
        path = Path(source).expanduser()
        if not path.is_absolute():
            raise ValueError("video_url must be an HTTP(S) URL or an absolute local video path")
    else:
        if not local_video or folder_paths is None:
            raise ValueError("Set video_url or select/upload local_video")
        root = Path(folder_paths.get_input_directory()).resolve()
        path = (root / local_video).resolve()
        if not path.is_relative_to(root):
            raise ValueError("local_video must be inside the ComfyUI input directory")
    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Input must be an existing supported video file")
    key = object_key(config.get("tencent_cos_input_prefix", "mps-super-resolution/input/"),
                     f"{uuid.uuid4().hex}_{safe_name(path.stem)}{path.suffix.lower()}")
    throw_exception_if_processing_interrupted()
    try:
        cos.upload_file(Bucket=config["tencent_cos_bucket"], Key=key, LocalFilePath=str(path))
    except (CosClientError, CosServiceError):
        raise RuntimeError("COS input upload failed; check credentials, bucket region and write permission") from None
    return {"Type": "COS", "CosInputInfo": {
        "Bucket": config["tencent_cos_bucket"], "Region": config["tencent_region"], "Object": "/" + key,
    }}


def build_payload(input_info, scene, resolution, config, run_id, output_base_name):
    if scene not in TEMPLATES or resolution not in RESOLUTIONS:
        raise ValueError("Select a supported MPS scene and resolution")
    key = object_key(config.get("tencent_cos_output_prefix", "mps-super-resolution/output/"),
                     f"{safe_name(output_base_name or 'enhanced')}_{run_id}.{{format}}")
    return {
        "InputInfo": input_info,
        "OutputStorage": {"Type": "COS", "CosOutputStorage": {
            "Bucket": config["tencent_cos_bucket"], "Region": config["tencent_region"],
        }},
        "MediaProcessTask": {"TranscodeTaskSet": [{
            "Definition": TEMPLATES[scene][RESOLUTIONS.index(resolution)], "OutputObjectPath": "/" + key,
        }]},
        "SessionId": run_id,
    }


def wait_for_completion(client, task_id, definition, config, progress):
    deadline = time.monotonic() + config["tencent_max_wait_seconds"]
    request = models.DescribeTaskDetailRequest()
    request.TaskId = task_id
    while time.monotonic() < deadline:
        throw_exception_if_processing_interrupted()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # The SDK copies HttpProfile.reqTimeout into the connection at construction.
        client.request.conn.timeout = min(config["tencent_request_timeout"], remaining)
        try:
            detail = json.loads(client.DescribeTaskDetail(request).to_json_string())
        except TencentCloudSDKException as exc:
            raise RuntimeError(f"MPS query failed: code={exc.get_code()}, task_id={task_id}") from None
        workflow = detail.get("WorkflowTask") or {}
        if workflow.get("ErrCode"):
            raise RuntimeError(f"MPS workflow failed: code={workflow['ErrCode']}, task_id={task_id}")
        result = next((item.get("TranscodeTask") for item in workflow.get("MediaProcessResultSet", [])
                       if item.get("Type") == "Transcode" and
                       (definition is None or ((item.get("TranscodeTask") or {}).get("Input") or {}).get("Definition") == definition)), None)
        if result:
            if result.get("Status") == "FAIL" or result.get("ErrCode") or result.get("ErrCodeExt"):
                code = result.get("ErrCodeExt") or result.get("ErrCode") or "FAIL"
                raise RuntimeError(f"MPS transcode failed: code={code}, task_id={task_id}")
            if result.get("Status") == "SUCCESS":
                if not result.get("Output") or not result["Output"].get("Path"):
                    raise RuntimeError(f"MPS succeeded without an output path: task_id={task_id}")
                return result["Output"]
            if progress is not None and result.get("Progress") is not None:
                progress.update_absolute(25 + int(max(0, min(100, result["Progress"])) * 0.6))
        if detail.get("Status") == "FINISH" or workflow.get("Status") == "FINISH":
            raise RuntimeError(f"MPS finished without a successful transcode result: task_id={task_id}")
        next_poll = min(deadline, time.monotonic() + config["tencent_poll_interval"])
        while time.monotonic() < next_poll:
            throw_exception_if_processing_interrupted()
            time.sleep(min(0.25, max(0, next_poll - time.monotonic())))
    raise TimeoutError(f"MPS wait timed out; the cloud task may still be running: task_id={task_id}")


def result_directory():
    directory = Path(folder_paths.get_output_directory()) if folder_paths else NODE_DIR / "output"
    return directory / "tencent_mps_video_enhance"


def task_record_path(task_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
        raise ValueError("Invalid resume_task_id")
    return result_directory() / "tasks" / f"{task_id}.json"


class IncompleteCOSDownload(RuntimeError):
    pass


def download_result(cos, output, config, task_id, output_base_name):
    storage = output.get("OutputStorage") or {"Type": "COS", "CosOutputStorage": {
        "Bucket": config["tencent_cos_bucket"], "Region": config["tencent_region"],
    }}
    info = storage.get("CosOutputStorage") or {}
    if storage.get("Type") != "COS" or info.get("Bucket") != config["tencent_cos_bucket"] or info.get("Region") != config["tencent_region"]:
        raise RuntimeError(f"Unexpected MPS output storage: task_id={task_id}")
    key = output["Path"].lstrip("/")
    suffix = Path(key).suffix.lower()
    if not key or suffix not in VIDEO_EXTENSIONS:
        raise RuntimeError(f"Unsupported MPS output video path: task_id={task_id}")
    directory = result_directory()
    directory.mkdir(parents=True, exist_ok=True)
    # Keep the cloud result recoverable even after MPS task history expires.
    record = task_record_path(task_id)
    record.parent.mkdir(parents=True, exist_ok=True)
    record_temporary = record.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        record_temporary.write_text(json.dumps({**output, "OutputStorage": storage}), encoding="utf-8")
        record_temporary.replace(record)
    finally:
        record_temporary.unlink(missing_ok=True)
    destination = directory / f"{safe_name(output_base_name or 'enhanced')}_{uuid.uuid4().hex}{suffix}"
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, 4):
        body = None
        retryable = True
        try:
            throw_exception_if_processing_interrupted()
            response = cos.get_object(Bucket=info["Bucket"], Key=key)
            body = response["Body"].get_raw_stream()
            headers = {name.lower(): value for name, value in response.items() if name != "Body"}
            expected = headers.get("content-length", output.get("Size"))
            digest = hashlib.md5()
            received = 0
            with temporary.open("wb") as handle:
                for chunk in body.stream(1024 * 1024, decode_content=False):
                    throw_exception_if_processing_interrupted()
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
            if received == 0 or (expected is not None and received != int(expected)):
                raise IncompleteCOSDownload(f"incomplete file: received={received}, expected={expected}")
            etag = headers.get("etag", "").strip('"')
            if re.fullmatch(r"[A-Fa-f0-9]{32}", etag) and digest.hexdigest() != etag.lower():
                raise IncompleteCOSDownload("file checksum mismatch")
            throw_exception_if_processing_interrupted()
            url = cos.get_presigned_url(Method="GET", Bucket=info["Bucket"], Key=key, Expired=86400)
            temporary.replace(destination)
            return str(destination), url, task_id
        except IncompleteCOSDownload as exc:
            reason = str(exc)
        except CosServiceError as exc:
            status = int(exc.get_status_code())
            reason = f"COS HTTP {status}"
            retryable = status in (408, 429) or status >= 500
        except (CosClientError, StreamHTTPError) as exc:
            reason = type(exc).__name__
        finally:
            if body is not None:
                body.close()
            temporary.unlink(missing_ok=True)
        if not retryable or attempt == 3:
            raise RuntimeError(f"COS download failed after {attempt} attempt(s): {reason}; "
                               f"set resume_task_id={task_id} to retry this result without submitting a new MPS task") from None
        print(f"[Tencent MPS] Download attempt {attempt}/3 failed ({reason}); retrying task_id={task_id}", flush=True)
        deadline = time.monotonic() + attempt
        while time.monotonic() < deadline:
            throw_exception_if_processing_interrupted()
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


class TencentMPSVideoEnhance:
    CATEGORY = "Tencent/MPS"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("local_video_path", "cos_video_url", "task_id")
    FUNCTION = "upscale"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_url": ("STRING", {"default": "", "tooltip": "HTTP(S) video URL or absolute local path. Leave empty to select/upload local_video."}),
                "scene": (list(TEMPLATES), {"default": "真人"}),
                "output_resolution": (list(RESOLUTIONS), {"default": "1080p"}),
            },
            "optional": {
                "local_video": (input_video_files(), {"las_video_upload": True}),
                "output_base_name": ("STRING", {"default": ""}),
                "resume_task_id": ("STRING", {"default": "", "tooltip": "填写原任务 ID 可恢复查询和下载，不再提交增强任务。留空则创建新任务。"}),
            },
        }

    def upscale(self, video_url, scene, output_resolution, local_video="", output_base_name="", resume_task_id=""):
        task_id = str(resume_task_id or "").strip()
        if task_id and not re.fullmatch(r"\d+-WorkflowTask-[A-Za-z0-9]+", task_id):
            raise ValueError("resume_task_id must be a Tencent MPS ID such as 123-WorkflowTask-abc. "
                             "Leave it empty to process a new video; 'video' and ComfyUI prompt IDs are not valid MPS task IDs.")
        config = load_config()
        if task_id:
            record = task_record_path(task_id)
            client, cos = create_clients(config)
            print(f"[Tencent MPS] Resuming task: {task_id}", flush=True)
            output = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else wait_for_completion(client, task_id, None, config, None)
            return download_result(cos, output, config, task_id, output_base_name)
        run_id = uuid.uuid4().hex
        payload = build_payload({}, scene, output_resolution, config, run_id, output_base_name)
        client, cos = create_clients(config)
        progress = ProgressBar(100) if ProgressBar else None
        if progress:
            progress.update_absolute(5)
        payload["InputInfo"] = resolve_input(video_url, local_video, config, cos)
        throw_exception_if_processing_interrupted()
        request = models.ProcessMediaRequest()
        request.from_json_string(json.dumps(payload))
        try:
            submitted = client.ProcessMedia(request)
        except TencentCloudSDKException as exc:
            raise RuntimeError(f"MPS submission failed: code={exc.get_code()}, session_id={run_id}; check the MPS console before submitting again") from None
        task_id = submitted.TaskId
        if not task_id:
            raise RuntimeError(f"MPS response missing TaskId; check the MPS console: session_id={run_id}")
        print(f"[Tencent MPS] Task submitted: {task_id}", flush=True)
        if progress:
            progress.update_absolute(25)
        output = wait_for_completion(client, task_id, payload["MediaProcessTask"]["TranscodeTaskSet"][0]["Definition"], config, progress)
        if progress:
            progress.update_absolute(85)
        result = download_result(cos, output, config, task_id, output_base_name)
        if progress:
            progress.update_absolute(100)
        print(f"[Tencent MPS] Task finished: {task_id}", flush=True)
        return result


NODE_CLASS_MAPPINGS = {"TencentMPSVideoEnhance": TencentMPSVideoEnhance}
NODE_DISPLAY_NAME_MAPPINGS = {"TencentMPSVideoEnhance": "Tencent MPS Video Super Resolution (COS)"}
