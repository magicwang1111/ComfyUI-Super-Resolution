import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests


NODE_DIR = Path(__file__).resolve().parent
LOCAL_CONFIG_PATH = NODE_DIR / "config.mediakit.local.json"
DEFAULT_BASE_URL = "https://mediakit.cn-beijing.volces.com"
VIDEO_EXTENSIONS = {
    ".mp4", ".flv", ".ts", ".avi", ".mov", ".wmv", ".mkv", ".webm",
    ".m4v", ".mpeg", ".mpg",
}
RESOLUTION_ALIASES = {"1440p": "2k", "2160p": "4k"}
TERMINAL_FAILURE_STATUSES = {"failed", "canceled", "cancelled"}


def load_config():
    try:
        config = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Missing local config: {LOCAL_CONFIG_PATH}. "
            "Copy config.mediakit.local.example.json to config.mediakit.local.json."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid config.mediakit.local.json: {exc}") from exc

    if not isinstance(config, dict):
        raise RuntimeError("config.mediakit.local.json must be a JSON object")

    required = (
        "api_key",
        "oss_endpoint",
        "oss_access_key_id",
        "oss_access_key_secret",
        "oss_bucket",
    )
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if missing:
        raise RuntimeError(
            f"Missing config.mediakit.local.json fields: {', '.join(missing)}"
        )
    return config


def output_directory():
    try:
        import folder_paths

        directory = Path(folder_paths.get_output_directory())
    except ImportError:
        directory = NODE_DIR / "output"
    directory = directory / "volcengine_video_enhance"
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


def resolve_local_video(local_video):
    local_video = str(local_video or "").strip()
    if not local_video:
        return None

    direct_path = Path(local_video).expanduser()
    if direct_path.is_file():
        return direct_path.resolve()

    try:
        import folder_paths

        if folder_paths.exists_annotated_filepath(local_video):
            return Path(folder_paths.get_annotated_filepath(local_video)).resolve()
    except ImportError:
        pass

    raise ValueError(f"local_video is not a valid ComfyUI input video: {local_video}")


def safe_component(value, fallback):
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(value or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def normalized_endpoint(endpoint):
    endpoint = str(endpoint).strip().rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return endpoint


def oss_bucket(config):
    try:
        import oss2
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: run python -m pip install -r requirements.txt"
        ) from exc

    auth = oss2.Auth(config["oss_access_key_id"], config["oss_access_key_secret"])
    return oss2.Bucket(
        auth,
        normalized_endpoint(config["oss_endpoint"]),
        config["oss_bucket"],
    )


def object_key(prefix, filename):
    prefix = str(prefix or "").strip("/\\")
    return f"{prefix}/{filename}" if prefix else filename


def sign_oss_url(bucket, key, config):
    expires = max(60, int(config.get("oss_signed_url_expires", 86400)))
    return bucket.sign_url("GET", key, expires)


def upload_local_input(source_path, config):
    suffix = source_path.suffix.lower() or ".mp4"
    filename = f"{uuid.uuid4().hex}_{safe_component(source_path.stem, 'input')}{suffix}"
    key = object_key(
        config.get("oss_prefix", "GouMee-super-resolution/input"),
        filename,
    )
    bucket = oss_bucket(config)
    try:
        bucket.put_object_from_file(key, str(source_path))
    except Exception as exc:
        raise RuntimeError(f"Upload input video to OSS failed: {exc}") from exc
    return sign_oss_url(bucket, key, config)


def sign_oss_path(oss_url, config):
    parsed = urlparse(oss_url)
    bucket_name = parsed.netloc
    key = unquote(parsed.path.lstrip("/"))
    if not bucket_name or not key:
        raise ValueError(f"Invalid OSS path: {oss_url}")
    if bucket_name != config["oss_bucket"]:
        raise ValueError(
            f"OSS path bucket '{bucket_name}' does not match configured bucket "
            f"'{config['oss_bucket']}'"
        )
    return sign_oss_url(oss_bucket(config), key, config)


def resolve_source_url(video_url, local_video, config):
    video_url = str(video_url or "").strip()
    if video_url.startswith(("http://", "https://")):
        return video_url
    if video_url.startswith("oss://"):
        return sign_oss_path(video_url, config)
    if video_url:
        source_path = Path(video_url).expanduser()
        if not source_path.is_file():
            raise ValueError(
                "video_url must be an HTTP(S) OSS URL, oss:// path, "
                "or existing local video path"
            )
        return upload_local_input(source_path.resolve(), config)

    source_path = resolve_local_video(local_video)
    if source_path is None:
        raise ValueError("Set video_url, or select/upload a local_video")
    return upload_local_input(source_path, config)


def result_file_name(result_url, task_id, output_base_name):
    suffix = Path(urlparse(result_url).path).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        suffix = ".mp4"
    if str(output_base_name or "").strip():
        stem = safe_component(output_base_name, task_id)
    else:
        stem = safe_component(task_id, "enhanced_video")
    return f"{stem}{suffix}"


def download_file(url, destination):
    try:
        with requests.get(url, stream=True, timeout=(30, 1800)) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
    except requests.RequestException as exc:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Download enhanced video failed: {exc}") from exc


def upload_output(source_path, task_id, config):
    filename = f"{safe_component(task_id, 'task')}_{source_path.name}"
    key = object_key(
        config.get("oss_output_prefix", "GouMee-super-resolution/output"),
        filename,
    )
    bucket = oss_bucket(config)
    try:
        bucket.put_object_from_file(key, str(source_path))
    except Exception as exc:
        raise RuntimeError(f"Upload enhanced video to OSS failed: {exc}") from exc
    return sign_oss_url(bucket, key, config)


def response_detail(response):
    try:
        return response.json()
    except ValueError:
        return response.text


def ensure_api_success(response, action):
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(
            f"{action} failed (HTTP {response.status_code}): {response_detail(response)}"
        ) from exc

    payload = response_detail(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{action} returned a non-JSON response: {payload}")
    if payload.get("success") is False:
        raise RuntimeError(f"{action} failed: {payload.get('error') or payload}")
    return payload


class VolcengineVideoEnhance:
    CATEGORY = "Volcengine/AI MediaKit"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("local_video_path", "oss_video_url", "task_id")
    FUNCTION = "upscale"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_url": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": (
                        "OSS HTTP(S) URL, oss://bucket/key, or local absolute path. "
                        "Local files are uploaded to OSS first. Leave empty to use local_video."
                    ),
                }),
                "output_resolution": (
                    ["720p", "1080p", "2k", "4k", "8k", "240p", "360p", "480p", "540p",
                     "1440p", "2160p"],
                    {"default": "1080p"},
                ),
            },
            "optional": {
                "local_video": (input_video_files(), {
                    "tooltip": (
                        "Select/upload a ComfyUI input video. It is uploaded to OSS "
                        "and submitted with a signed URL when video_url is empty."
                    ),
                    "las_video_upload": True,
                }),
                "output_base_name": ("STRING", {"default": "", "multiline": False}),
                "preserve_audio": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Compatibility option from the previous LAS node; AI MediaKit preserves audio.",
                }),
                "output_quality_mode": (
                    ["compatible", "balanced", "master"],
                    {
                        "default": "compatible",
                        "tooltip": "Compatibility option from the previous LAS node; ignored by AI MediaKit.",
                    },
                ),
                "tool_version": (["standard", "professional"], {"default": "standard"}),
                "scene": (
                    ["aigc", "common", "ugc", "short_series", "old_film"],
                    {"default": "aigc"},
                ),
                "bitrate_level": (["medium", "high", "low"], {"default": "medium"}),
                "fps": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 120,
                    "step": 1,
                    "tooltip": "0 keeps the source frame rate; otherwise valid range is 15-120.",
                }),
                "bit_depth": (["auto", "8", "10", "12"], {"default": "auto"}),
            },
        }

    def upscale(
        self,
        video_url,
        output_resolution,
        output_base_name="",
        preserve_audio=True,
        output_quality_mode="compatible",
        local_video="",
        tool_version="standard",
        scene="aigc",
        bitrate_level="medium",
        fps=0,
        bit_depth="auto",
    ):
        del preserve_audio, output_quality_mode
        config = load_config()
        source_url = resolve_source_url(video_url, local_video, config)
        payload = self._build_payload(
            source_url,
            output_resolution,
            tool_version,
            scene,
            bitrate_level,
            fps,
            bit_depth,
        )
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        }
        base_url = str(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        response = requests.post(
            f"{base_url}/api/v1/tools/enhance-video",
            json=payload,
            headers=headers,
            timeout=60,
        )
        submitted = ensure_api_success(response, "Submit video enhance task")
        task_id = str(submitted.get("task_id") or "").strip()
        if not task_id:
            raise RuntimeError("Submit response did not include task_id")

        result = self._wait_for_completion(base_url, headers, task_id, config)
        result_url = str(result.get("video_url") or "").strip()
        if not result_url.startswith(("http://", "https://")):
            raise RuntimeError(
                "Task completed but result.video_url was not an HTTP(S) download URL"
            )

        local_path = output_directory() / result_file_name(
            result_url, task_id, output_base_name
        )
        download_file(result_url, local_path)
        oss_url = upload_output(local_path, task_id, config)
        return (str(local_path), oss_url, task_id)

    @staticmethod
    def _build_payload(
        source_url,
        output_resolution,
        tool_version,
        scene,
        bitrate_level,
        fps,
        bit_depth,
    ):
        resolution = RESOLUTION_ALIASES.get(output_resolution, output_resolution)
        payload = {
            "video_url": source_url,
            "tool_version": tool_version,
            "resolution": resolution,
            "bitrate_level": bitrate_level,
            "client_token": uuid.uuid4().hex,
        }
        if tool_version == "standard":
            payload["scene"] = scene
        if int(fps) != 0:
            if not 15 <= int(fps) <= 120:
                raise ValueError("fps must be 0 (keep source) or between 15 and 120")
            payload["fps"] = int(fps)
        if bit_depth != "auto":
            if tool_version != "professional":
                raise ValueError("bit_depth is only supported by professional mode")
            payload["bit_depth"] = int(bit_depth)
        return payload

    @staticmethod
    def _wait_for_completion(base_url, headers, task_id, config):
        interval = max(1, int(config.get("poll_interval_seconds", 10)))
        timeout = max(interval, int(config.get("poll_timeout_seconds", 10800)))
        deadline = time.monotonic() + timeout
        task_url = f"{base_url}/api/v1/tasks/{quote(task_id, safe='')}"

        while time.monotonic() < deadline:
            response = requests.get(task_url, headers=headers, timeout=60)
            payload = ensure_api_success(response, f"Query task {task_id}")
            status = str(payload.get("status") or "").lower()
            if status == "completed":
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(
                        f"Task {task_id} completed without a valid result object"
                    )
                return result
            if status in TERMINAL_FAILURE_STATUSES:
                error = payload.get("error") or "unknown error"
                raise RuntimeError(f"Video enhance task {status}: {error}")
            time.sleep(interval)

        raise TimeoutError(
            f"Timed out waiting for video enhance task after {timeout} seconds: {task_id}"
        )


NODE_CLASS_MAPPINGS = {"VolcengineVideoEnhance": VolcengineVideoEnhance}
NODE_DISPLAY_NAME_MAPPINGS = {
    "VolcengineVideoEnhance": "Volcengine AI MediaKit Video Enhance (OSS)",
}
