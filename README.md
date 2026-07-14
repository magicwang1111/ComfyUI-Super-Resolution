# ComfyUI Volcengine Video Super Resolution

调用火山引擎 LAS `las_video_super_resolution` 算子，将视频超分至 720p、1080p、1440p 或 2160p。

## 配置

1. 安装依赖：`python -m pip install -r requirements.txt`。
2. 将 `config.local.example.json` 复制为 `config.local.json`，填写 API Key 与 TOS 凭据。
3. 重启 ComfyUI，在 `Volcengine/LAS` 分类使用 **LAS Video Super Resolution**。

节点只读取 `config.local.json`；该文件已被 Git 忽略。

## 存储方式

- 输入：直接填写 `tos://bucket/key`，或填写 HTTP(S) 视频 URL、本地视频绝对路径。HTTP(S) URL（包括 Seedance 的签名 URL）会先下载，再上传到 `tos_bucket` 的 `tos_input_prefix`。
- 输出：LAS 将结果写入同一 `tos_bucket` 的 `tos_output_prefix`，节点再下载到 ComfyUI 的 `output/volcengine_video_super_resolution` 目录。

因此，`tos_bucket` 必须与 LAS 服务同主账号、同地域，并同时具备输入对象读取和输出目录写入权限。

## 节点参数

- `video_url`：TOS 路径或本地视频绝对路径。
- `output_resolution`：`720p`、`1080p`、`1440p` 或 `2160p`。
- `output_base_name`：可选的结果文件基础名。
- `preserve_audio`：是否保留原音频。
- `output_quality_mode`：`compatible`、`balanced` 或 `master`。
