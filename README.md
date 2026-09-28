# ComfyUI Super Resolution

本插件提供三个彼此独立的视频增强节点：

- **LAS Video Super Resolution**：原有 LAS `las_video_super_resolution` 算子和 TOS 流程。
- **Volcengine AI MediaKit Video Enhance (OSS)**：新增 AI MediaKit `enhance-video` API 和阿里云 OSS 流程。
- **Tencent MPS Video Super Resolution (COS)**：腾讯云 MPS 预设增强模板和 COS 流程。

## 安装依赖

在 ComfyUI Python 环境执行：

```powershell
python -m pip install -r requirements.txt
```

## 原有 LAS 节点

- 节点 ID：`LASVideoSuperResolution`
- 分类：`Volcengine/LAS`

LAS 节点代码、参数、`/api/v1/submit`、`/api/v1/poll`、TOS 输入输出和本地下载流程保持不变。

1. 将 `config.local.example.json` 复制为 `config.local.json`。
2. 填写 LAS API Key 和火山引擎 TOS 凭据。
3. 重启 ComfyUI，添加 **LAS Video Super Resolution**。

输入支持 `tos://bucket/key`、HTTP(S) 视频 URL、本地绝对路径，或节点中的 `local_video`。HTTP(S) 和本地文件会先上传到配置的 `tos_bucket/tos_input_prefix`。LAS 结果写入 `tos_output_prefix`，随后下载到 `output/volcengine_video_super_resolution`。

输出分辨率支持 `720p`、`1080p`、`1440p`、`2160p`，并保持原始视频宽高比。

## 新增 AI MediaKit 节点

- 节点 ID：`VolcengineVideoEnhance`
- 分类：`Volcengine/AI MediaKit`

完整链路：

`本地视频 -> 阿里云 OSS 输入目录 -> OSS 签名 URL -> AI MediaKit -> 下载临时结果 -> 阿里云 OSS 输出目录`

1. 将 `config.mediakit.local.example.json` 复制为 `config.mediakit.local.json`。
2. 填写 AI MediaKit API Key 和阿里云 OSS 配置。
3. 重启 ComfyUI，添加 **Volcengine AI MediaKit Video Enhance (OSS)**。

### 输入和输出

- HTTP(S) OSS 公网或签名链接：直接提交。
- `oss://bucket/key`：使用本地 OSS 凭据生成签名链接后提交。
- 本地绝对路径或 `local_video`：先上传到 `oss_prefix`，再提交签名链接。
- 处理结果下载到 `output/volcengine_video_enhance`，随后上传到 `oss_output_prefix`。
- 返回本地视频路径、OSS 输出签名链接和 AI MediaKit 任务 ID。

### 参数

- `output_resolution`：支持 `240p`、`360p`、`480p`、`540p`、`720p`、`1080p`、`2k`、`4k`、`8k`。兼容值 `1440p` 映射为 `2k`，`2160p` 映射为 `4k`。
- `tool_version`：`standard` 或 `professional`。
- `scene`：标准版支持 `common`、`ugc`、`short_series`、`aigc`、`old_film`。
- `bitrate_level`：`low`、`medium`、`high`。
- `fps`：`0` 保持原帧率；指定值范围为 15–120。
- `bit_depth`：仅专业版支持 8/10/12 bit；`auto` 不传该参数。
- `output_base_name`：可选的本地输出文件名。

## 密钥安全

`config.local.json`、`config.mediakit.local.json` 和 `config.tencent.local.json` 均被 Git 忽略。密钥只在本地配置中填写，不放入节点参数或工作流。

## 腾讯云 MPS 节点

- 节点 ID：`TencentMPSVideoEnhance`；分类：`Tencent/MPS`。
- 将 `config.tencent.local.example.json` 复制为 `config.tencent.local.json`，填写 `tencent_secret_id`、`tencent_secret_key`、地域和 COS 桶名称（含 APPID 后缀）。安装依赖并重启 ComfyUI 后添加节点。
- 账户需开通 MPS 并授予 MPS 访问 COS 的权限，本地凭据需有输入上传、输出读取权限。
- `video_url` 接受 HTTP(S) 视频链接或本地绝对路径；留空时使用 `local_video`，可通过上传按钮选择视频。本地视频会上传到 COS，HTTP(S) 链接直接提交且需保持云端可访问。
- 场景支持真人、漫剧、老片/低清修复、真人小脸优化、漫剧小脸优化和 Seedance2；分辨率支持 `720p`、`1080p`、`2k`、`4k`，默认真人、1080p。使用文档中的 24 个预设模板，不覆盖其帧率、音频、码率或其他增强参数。
- 输出为本地视频路径、有效期 24 小时的 COS 签名链接和任务 ID。本地文件写入 `output/tencent_mps_video_enhance`，文件名附带唯一标识。
- 下载遇到连接错误、文件不完整或临时服务错误时最多尝试 3 次；检查文件大小，并在 COS 返回单段 MD5 ETag 时校验内容。完整校验通过后才保存为正式视频，权限错误和用户取消不会反复重试。
- 下载失败后，在 `resume_task_id` 中填写报错里的任务 ID，再运行即可恢复原任务，不会上传输入或重新提交增强。恢复时忽略输入视频、场景和分辨率；留空仍会创建新任务。云端结果位置保存在输出目录的 `tasks` 子目录中，便于重启后恢复；没有本地记录时会向 MPS 查询，需该任务仍在云端查询保留期内。
- `resume_task_id` 只接受腾讯云 `数字-WorkflowTask-…` 格式的任务 ID，不接受 ComfyUI 任务编号。旧工作流把上传按钮值 `video` 错位带入该字段时，重新加载工作流会自动清空；其他无效值在请求云端前报错。
- 默认 COS 输入/输出前缀为 `mps-super-resolution/input/` 和 `mps-super-resolution/output/`，可通过配置修改。云端文件保留，节点不自动删除。
- `tencent_request_timeout` 默认 600 秒，`tencent_poll_interval` 默认 5 秒，`tencent_max_wait_seconds` 默认 3600 秒（从提交成功后开始等待）。超时、中断或查询失败不会自动重新提交；任务 ID 会打印在控制台，云端任务可能仍继续运行，可在 MPS 控制台查看。中断会在轮询等待和下载分块之间检查，进行中的网络请求需等待返回或超时。
- 超分使用增强模板，与字幕模板 `tencent_subtitle_definition` 无关。按腾讯云实际用量计费，老片/低清预设使用大模型修复，其余使用大模型增强。
