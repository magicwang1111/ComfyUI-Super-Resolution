# ComfyUI Volcengine Video Super Resolution

调用火山引擎 LAS `las_video_super_resolution` 算子，对输入视频做清晰度增强和分辨率提升，输出 720p、1080p、1440p 或 2160p 视频。

## 配置

1. 安装依赖：`python -m pip install -r requirements.txt`
2. 将 `config.local.example.json` 复制为 `config.local.json`，填写 LAS API Key 和 TOS 凭据。
3. 重启 ComfyUI，在 `Volcengine/LAS` 分类使用 **LAS Video Super Resolution**。

节点只读取 `config.local.json`；该文件已被 Git 忽略，不会提交真实密钥。

## 存储方式

- 输入：支持 `tos://bucket/key`、HTTP(S) 视频 URL、本地绝对路径，或节点里的 `local_video` 上传控件。HTTP(S)、本地绝对路径、`local_video` 都会先上传到配置里的 `tos_bucket/tos_input_prefix`。
- 输出：LAS 将结果写入同一 `tos_bucket` 的 `tos_output_prefix`，节点再下载到 ComfyUI 的 `output/volcengine_video_super_resolution` 目录。
- 分辨率：对可探测到本地源文件的输入，节点会读取原视频宽高，按所选 720p/1080p/1440p/2160p 档位的最大像素数计算目标宽高，并保持原始宽高比。`tos://` 直传输入无法本地探测尺寸时，会回退为只传目标宽度。

因此，`tos_bucket` 必须和 LAS 服务同主账号、同地域，并且具备输入对象读取和输出目录写入权限。

## 节点参数

- `video_url`：TOS 路径、HTTP(S) 视频 URL 或本地视频绝对路径；留空时使用 `local_video`。
- `local_video`：从 ComfyUI input 目录选择或上传本地视频，节点会自动上传到 TOS。
- `output_resolution`：`720p`、`1080p`、`1440p` 或 `2160p`。
- `output_base_name`：可选的结果文件基础名。
- `preserve_audio`：是否保留原音频。
- `output_quality_mode`：`compatible`、`balanced` 或 `master`。
