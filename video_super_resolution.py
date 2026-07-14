import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests


NODE_DIR = Path(__file__).resolve().parent
LOCAL_CONFIG_PATH = NODE_DIR / "config.local.json"
OPERATOR_ID = "las_video_super_resolution"
OPERATOR_VERSION = "v1"
RESOLUTION_WIDTHS = {"720p": 1280, "1080p": 1920, "1440p": 2560, "2160p": 3840}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMEOUT"}


def load_config():
    try:
        config = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"找不到本地配置文件: {LOCAL_CONFIG_PATH}；请由 config.local.example.json 复制创建"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"config.local.json 格式错误: {exc}") from exc

    if not isinstance(config, dict):
        raise RuntimeError("config.local.json 必须是 JSON 对象")

    required = (
        "api_key", "base_url", "tos_endpoint", "tos_region", "tos_access_key_id",
        "tos_access_key_secret", "tos_bucket",
    )
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if missing:
        raise RuntimeError(f"请先在 config.local.json 配置: {', '.join(missing)}")
    return config


def output_directory():
    try:
        import folder_paths

        directory = Path(folder_paths.get_output_directory())
    except ImportError:
        directory = NODE_DIR / "output"
    directory = directory / "volcengine_video_super_resolution"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_file_name(url, task_id):
    file_name = Path(urlparse(url).path).name or f"{task_id}.mp4"
    file_name = re.sub(r"[^A-Za-z0-9._-]", "_", file_name)
    return file_name if Path(file_name).suffix else f"{file_name}.mp4"


def tos_client(config):
    tos_fields = ("tos_endpoint", "tos_region", "tos_access_key_id", "tos_access_key_secret", "tos_bucket")
    missing = [key for key in tos_fields if not str(config.get(key, "")).strip()]
    if missing:
        raise RuntimeError(f"请先在 config.local.json 配置 TOS: {', '.join(missing)}")
    try:
        import tos
    except ImportError as exc:
        raise RuntimeError("缺少 tos，请执行 python -m pip install -r requirements.txt") from exc
    return tos.TosClientV2(
        config["tos_access_key_id"],
        config["tos_access_key_secret"],
        config["tos_endpoint"],
        config["tos_region"],
    )


def tos_path(bucket, key):
    return f"tos://{bucket}/{key.lstrip('/')}"


def output_tos_path(config):
    prefix = str(config.get("tos_output_prefix", "video-super-resolution/output")).strip("/\\")
    return tos_path(config["tos_bucket"], f"{prefix}/" if prefix else "")


def upload_to_tos(source_path, config):
    client = tos_client(config)
    prefix = str(config.get("tos_input_prefix", "video-super-resolution/input")).strip("/\\")
    object_name = f"{uuid.uuid4().hex}_{source_path.name}"
    object_key = f"{prefix}/{object_name}" if prefix else object_name
    try:
        client.put_object_from_file(
            bucket=config["tos_bucket"], key=object_key, file_path=str(source_path)
        )
    except Exception as exc:
        raise RuntimeError(f"上传 TOS 失败: {exc}") from exc
    return tos_path(config["tos_bucket"], object_key)


def upload_http_url_to_tos(source_url, config):
    source_name = safe_file_name(source_url, uuid.uuid4().hex)
    suffix = Path(source_name).suffix or ".mp4"
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="comfyui_super_resolution_", suffix=suffix
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        with requests.get(source_url, stream=True, timeout=(30, 600)) as response:
            response.raise_for_status()
            with temporary_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        return upload_to_tos(temporary_path, config)
    except requests.RequestException as exc:
        raise RuntimeError(f"下载输入视频 URL 失败: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def download_from_tos(tos_url, destination, config):
    parsed = urlparse(tos_url)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise RuntimeError(f"无效的 TOS 结果路径: {tos_url}")
    try:
        tos_client(config).get_object_to_file(bucket=bucket, key=key, file_path=str(destination))
    except Exception as exc:
        raise RuntimeError(f"从 TOS 下载结果失败: {exc}") from exc


class LASVideoSuperResolution:
    CATEGORY = "Volcengine/LAS"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("local_video_path", "result_video_url", "task_id")
    FUNCTION = "upscale"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_url": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "TOS 路径、HTTP(S) 视频 URL 或本地绝对路径；后两者会自动上传到配置的 TOS",
                }),
                "output_resolution": (["720p", "1080p", "1440p", "2160p"], {"default": "1080p"}),
            },
            "optional": {
                "output_base_name": ("STRING", {"default": "", "multiline": False}),
                "preserve_audio": ("BOOLEAN", {"default": True}),
                "output_quality_mode": (["compatible", "balanced", "master"], {"default": "compatible"}),
            },
        }

    def upscale(self, video_url, output_resolution, output_base_name="", preserve_audio=True,
                output_quality_mode="compatible"):
        config = load_config()
        if video_url.startswith("tos://"):
            source_url = video_url
        elif video_url.startswith(("http://", "https://")):
            source_url = upload_http_url_to_tos(video_url, config)
        else:
            source_path = Path(video_url).expanduser()
            if not source_path.is_file():
                raise ValueError("video_url 必须为 TOS 路径、HTTP(S) 视频 URL 或存在的本地视频绝对路径")
            source_url = upload_to_tos(source_path, config)
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        }
        data = {
            "video_url": source_url,
            "output_tos_path": output_tos_path(config),
            "target_width": RESOLUTION_WIDTHS[output_resolution],
            "preserve_audio": preserve_audio,
            "output_quality_mode": output_quality_mode,
        }
        if output_base_name.strip():
            data["output_base_name"] = output_base_name.strip()

        base_url = str(config["base_url"]).rstrip("/")
        response = requests.post(
            f"{base_url}/api/v1/submit", json={
                "operator_id": OPERATOR_ID,
                "operator_version": OPERATOR_VERSION,
                "data": data,
            }, headers=headers, timeout=60,
        )
        self._raise_for_api_error(response, "提交超分任务")
        task_id = response.json().get("metadata", {}).get("task_id")
        if not task_id:
            raise RuntimeError("提交超分任务未返回 task_id")

        result = self._wait_for_completion(base_url, headers, task_id, config)
        result_url = result.get("output_video_url") or result.get("output_video_tos_url")
        if not result_url:
            raise RuntimeError(
                "任务已完成但未返回 output_video_url 或 output_video_tos_url。"
            )
        local_path = output_directory() / safe_file_name(result_url, task_id)
        if result_url.startswith("tos://"):
            download_from_tos(result_url, local_path, config)
        else:
            self._download(result_url, local_path)
        return (str(local_path), result_url, task_id)

    def _wait_for_completion(self, base_url, headers, task_id, config):
        interval = max(1, int(config.get("poll_interval_seconds", 5)))
        timeout = max(interval, int(config.get("poll_timeout_seconds", 7200)))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = requests.post(
                f"{base_url}/api/v1/poll", json={
                    "operator_id": OPERATOR_ID,
                    "operator_version": OPERATOR_VERSION,
                    "task_id": task_id,
                }, headers=headers, timeout=60,
            )
            self._raise_for_api_error(response, f"查询任务 {task_id}")
            payload = response.json()
            metadata = payload.get("metadata", {})
            status = metadata.get("task_status", "")
            if status == "COMPLETED":
                return payload.get("data", {})
            if status in TERMINAL_STATUSES:
                raise RuntimeError(f"超分任务 {status}: {metadata.get('error_msg') or '未知错误'}")
            time.sleep(interval)
        raise TimeoutError(f"等待超分任务超时（{timeout} 秒）：{task_id}")

    @staticmethod
    def _raise_for_api_error(response, action):
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RuntimeError(f"{action}失败（HTTP {response.status_code}）: {detail}") from exc

    @staticmethod
    def _download(url, destination):
        with requests.get(url, stream=True, timeout=(30, 600)) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)


NODE_CLASS_MAPPINGS = {"LASVideoSuperResolution": LASVideoSuperResolution}
NODE_DISPLAY_NAME_MAPPINGS = {"LASVideoSuperResolution": "LAS Video Super Resolution"}
