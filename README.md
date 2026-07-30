# ComfyUI Super Resolution

本插件提供两个彼此独立的火山引擎视频增强节点：

- **LAS Video Super Resolution**：原有 LAS `las_video_super_resolution` 算子和 TOS 流程。
- **Volcengine AI MediaKit Video Enhance (OSS)**：新增 AI MediaKit `enhance-video` API 和阿里云 OSS 流程。

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

`config.local.json` 和 `config.mediakit.local.json` 均被 Git 忽略，真实密钥不会提交到仓库。
