import json
import math
import os
import re
import subprocess
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
RESOLUTION_MAX_PIXELS = {
    "720p": 927408,
    "1080p": 2086876,
    "1440p": 3709632,
    "2160p": 8347504,
}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMEOUT"}
VIDEO_EXTENSIONS = {
    ".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".mpeg", ".mpg",
}


def load_config():
    try:
        config = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Missing local config: {LOCAL_CONFIG_PATH}. Copy config.local.example.json to config.local.json."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid config.local.json: {exc}") from exc

    if not isinstance(config, dict):
        raise RuntimeError("config.local.json must be a JSON object")

    required = (
        "api_key", "base_url", "tos_endpoint", "tos_region", "tos_access_key_id",
        "tos_access_key_secret", "tos_bucket",
    )
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if missing:
        raise RuntimeError(f"Missing config.local.json fields: {', '.join(missing)}")
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


def input_video_files():
    try:
        import folder_paths

        input_dir = Path(folder_paths.get_input_directory())
        files = [item.name for item in input_dir.iterdir() if item.is_file()]
        if hasattr(folder_paths, "filter_files_content_types"):
            files = folder_paths.filter_files_content_types(files, ["video"])
        else:
            files = [name for name in files if Path(name).suffix.lower() in VIDEO_EXTENSIONS]
    except Exception:
        files = []
    return [""] + sorted(files)


def safe_file_name(url, task_id):
    file_name = Path(urlparse(url).path).name or f"{task_id}.mp4"
    file_name = re.sub(r"[^A-Za-z0-9._-]", "_", file_name)
    return file_name if Path(file_name).suffix else f"{file_name}.mp4"


def tos_client(config):
    tos_fields = ("tos_endpoint", "tos_region", "tos_access_key_id", "tos_access_key_secret", "tos_bucket")
    missing = [key for key in tos_fields if not str(config.get(key, "")).strip()]
    if missing:
        raise RuntimeError(f"Missing TOS config.local.json fields: {', '.join(missing)}")
    try:
        import tos
    except ImportError as exc:
        raise RuntimeError("Missing dependency: run python -m pip install -r requirements.txt") from exc
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
        raise RuntimeError(f"Upload to TOS failed: {exc}") from exc
    return tos_path(config["tos_bucket"], object_key)


def probe_video_size(source_path):
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "json", str(source_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Failed to probe input video size with ffprobe: {source_path}") from exc

    streams = json.loads(result.stdout or "{}").get("streams", [])
    if not streams:
        raise RuntimeError(f"No video stream found in input: {source_path}")
    width = int(streams[0].get("width") or 0)
    height = int(streams[0].get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid input video size: {source_path}")
    return width, height


def even_floor(value):
    return max(2, int(value) // 2 * 2)


def target_dimensions(output_resolution, source_size):
    if source_size is None:
        return {"target_width": RESOLUTION_WIDTHS[output_resolution]}

    source_width, source_height = source_size
    ratio = source_width / source_height
    max_pixels = RESOLUTION_MAX_PIXELS[output_resolution]
    target_height = math.sqrt(max_pixels / ratio)
    target_width = target_height * ratio
    width = even_floor(target_width)
    height = even_floor(target_height)

    while width * height > max_pixels:
        if width >= height:
            width -= 2
        else:
            height -= 2
    return {"target_width": width, "target_height": height}


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
        source_size = probe_video_size(temporary_path)
        return upload_to_tos(temporary_path, config), source_size
    except requests.RequestException as exc:
        raise RuntimeError(f"Download input video URL failed: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def resolve_local_video(local_video):
    local_video = str(local_video or "").strip()
    if not local_video:
        return None

    direct_path = Path(local_video).expanduser()
    if direct_path.is_file():
        return direct_path

    try:
        import folder_paths

        if folder_paths.exists_annotated_filepath(local_video):
            return Path(folder_paths.get_annotated_filepath(local_video))
    except ImportError:
        pass

    raise ValueError(f"local_video is not a valid ComfyUI input video: {local_video}")


def resolve_source_url(video_url, local_video, config):
    video_url = str(video_url or "").strip()
    if video_url.startswith("tos://"):
        return video_url, None
    if video_url.startswith(("http://", "https://")):
        return upload_http_url_to_tos(video_url, config)
    if video_url:
        source_path = Path(video_url).expanduser()
        if not source_path.is_file():
            raise ValueError("video_url must be a TOS path, HTTP(S) video URL, or existing local absolute path")
        return upload_to_tos(source_path, config), probe_video_size(source_path)

    source_path = resolve_local_video(local_video)
    if source_path is None:
        raise ValueError("Set video_url, or select/upload a local_video")
    return upload_to_tos(source_path, config), probe_video_size(source_path)


def download_from_tos(tos_url, destination, config):
    parsed = urlparse(tos_url)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise RuntimeError(f"Invalid TOS result path: {tos_url}")
    try:
        tos_client(config).get_object_to_file(bucket=bucket, key=key, file_path=str(destination))
    except Exception as exc:
        raise RuntimeError(f"Download result from TOS failed: {exc}") from exc


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
                    "tooltip": (
                        "TOS path, HTTP(S) video URL, or local absolute path. "
                        "HTTP(S) and local paths are uploaded to configured TOS. "
                        "Leave empty to use local_video."
                    ),
                }),
                "output_resolution": (["720p", "1080p", "1440p", "2160p"], {"default": "1080p"}),
            },
            "optional": {
                "local_video": (input_video_files(), {
                    "tooltip": "Select or upload a video from the ComfyUI input folder. Used only when video_url is empty.",
                    "las_video_upload": True,
                }),
                "output_base_name": ("STRING", {"default": "", "multiline": False}),
                "preserve_audio": ("BOOLEAN", {"default": True}),
                "output_quality_mode": (["compatible", "balanced", "master"], {"default": "compatible"}),
            },
        }

    def upscale(self, video_url, output_resolution, output_base_name="", preserve_audio=True,
                output_quality_mode="compatible", local_video=""):
        config = load_config()
        source_url, source_size = resolve_source_url(video_url, local_video, config)
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        }
        data = {
            "video_url": source_url,
            "output_tos_path": output_tos_path(config),
            "preserve_audio": preserve_audio,
            "output_quality_mode": output_quality_mode,
        }
        data.update(target_dimensions(output_resolution, source_size))
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
        self._raise_for_api_error(response, "submit super-resolution task")
        task_id = response.json().get("metadata", {}).get("task_id")
        if not task_id:
            raise RuntimeError("Submit response did not include metadata.task_id")

        result = self._wait_for_completion(base_url, headers, task_id, config)
        result_url = result.get("output_video_url") or result.get("output_video_tos_url")
        if not result_url:
            raise RuntimeError("Task completed but did not return output_video_url or output_video_tos_url")
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
            self._raise_for_api_error(response, f"poll task {task_id}")
            payload = response.json()
            metadata = payload.get("metadata", {})
            status = metadata.get("task_status", "")
            if status == "COMPLETED":
                return payload.get("data", {})
            if status in TERMINAL_STATUSES:
                raise RuntimeError(f"Super-resolution task {status}: {metadata.get('error_msg') or 'unknown error'}")
            time.sleep(interval)
        raise TimeoutError(f"Timed out waiting for super-resolution task after {timeout} seconds: {task_id}")

    @staticmethod
    def _raise_for_api_error(response, action):
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RuntimeError(f"{action} failed (HTTP {response.status_code}): {detail}") from exc

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
