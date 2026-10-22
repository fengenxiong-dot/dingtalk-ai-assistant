# 通过 OpenRouter 调用 OpenAI 模型

已增加专用 OpenRouter 适配。员工仍通过钉钉单聊操作，会话、检索、图片版本、白名单和额度机制不变。

## 本机只需填写一把密钥

项目根目录 `.env` 已切换为以下配置：

```dotenv
PROVIDER=openrouter
OPENROUTER_API_KEY=在本地填写你的OpenRouter密钥
OPENROUTER_TEXT_MODEL=openai/gpt-4.1-mini
OPENROUTER_IMAGE_MODEL=openai/gpt-image-2
```

在 [OpenRouter 密钥管理](https://openrouter.ai/keys) 获取项目 API Key，在本地填写 `OPENROUTER_API_KEY`。不要把 OpenAI 官网的 API Key 填在此处。默认模型 ID 带 `openai/` 前缀。

OpenRouter 模式使用独立的 `OPENROUTER_*` 配置，**忽略直连 OpenAI 的 `TEXT_API_KEY`、`IMAGE_API_KEY`、`TEXT_BASE_URL`、`IMAGE_BASE_URL`、`TEXT_MODEL`、`IMAGE_MODEL`**。无需修改或删除原有直连配置，也不会将 OpenAI 官网密钥发送给 OpenRouter。

`DINGTALK_*`、`ADMIN_TOKEN`、数据库、额度和图片保留设置均保持原值。切换脚本会检查其他原有配置未发生变化，但不会打印密钥。

## 检查与启动

```powershell
.\.venv\Scripts\python.exe -m app check
```

`provider` 应为 `openrouter`，`model_credentials_configured` 和 `dingtalk_configured` 应为 `true`。检查不联网、不产生费用。缺少 OpenRouter Key 时仍能运行检查和本地模拟演示；真实服务启动会报缺少 `OPENROUTER_API_KEY`，不会使用旧密钥回退调用。

填好后重启已有服务，使 `.env` 生效。三个终端分别运行：

```powershell
.\.venv\Scripts\python.exe -m app worker
```

```powershell
.\.venv\Scripts\python.exe -m app sender
```

```powershell
.\.venv\Scripts\python.exe -m app stream
```

如果管理 API 已启动，也需重启 `python -m app serve`。Docker 部署需重新构建并重建容器：

```powershell
docker compose --profile dingtalk up --build -d
```

员工白名单仍需开通，步骤见 [接入与验收](接入与验收.md)。先用自己的钉钉单聊测试文字，再测试一张图片及一次改图；正式调用会使用 OpenRouter 账户额度。

## 接口差异

不是仅修改网址。当前按 OpenRouter 官方文档实现：

| 能力 | OpenRouter 接口 | 请求要点 |
|---|---|---|
| 文字、历史摘要 | `POST https://openrouter.ai/api/v1/chat/completions` | 完整模型 ID，使用 OpenRouter Key |
| 生图 | `POST https://openrouter.ai/api/v1/images` | `model`、`prompt`、`n: 1` |
| 改图 | 同一个 `/images` 接口 | `input_references` 传入当前授权图片的 base64 data URL |

从 `data[0].b64_json` 读取图片。拒绝任意远程图片 URL 或 SVG 响应，继续使用本地图片校验和私有文件存储。

`TEXT_EXTRA_JSON`、`IMAGE_EXTRA_JSON` 可设置 OpenRouter 支持的额外参数；`IMAGE_EDIT_FORMAT` 只用于直连模式，OpenRouter 改图始终使用 `input_references`。

例如指定生图比例：

```dotenv
IMAGE_EXTRA_JSON={"aspect_ratio":"16:9","quality":"medium"}
```

应用强制每次一张图、关闭流式返回，并使用当前任务指定的来源图片，不能通过 extra 参数替换图片来源。默认 `provider.allow_fallbacks=false`，不自动尝试另一服务端点；管理员可通过 extra 的 `provider` 对象调整。

接口返回的 `usage`（包括可用时的 `cost`，单位美元）原样保存。现有人民币费用预留参数不会自动按汇率换算，实际费用以 OpenRouter 账单为准。

## 验证状态

已通过 57 项自动测试，包括 OpenRouter 接口契约、密钥隔离、配置迁移、错误脱敏、图片来源绑定、worker 保存流程和 Stream 诊断。自动测试使用模拟响应，不消耗额度。

2026-09-17 已通过真实密钥验证，以及两个默认模型的文字、生图和参考图编辑调用。测试图片为白底蓝色圆形，编辑后为白底橙色圆形，已人工查看输出。三次调用返回的 cost 合计为 0.0201812 美元，最终以账单为准。钉钉已获取访问令牌并建立 Stream 连接，员工白名单和消息往返尚待验收。

参考：[OpenRouter 快速开始](https://openrouter.ai/docs/quickstart)、[图片生成与参考图编辑](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)、[公开文本模型目录](https://openrouter.ai/api/v1/models)、[公开图片模型目录](https://openrouter.ai/api/v1/images/models)。

## 切换模式

- 离线体验：`PROVIDER=mock`，或执行 `python -m app demo`（始终为独立模拟演示）。
- 直连 OpenAI：`PROVIDER=openai`，填写原有 `TEXT_API_KEY` 与 `IMAGE_API_KEY`。
- OpenRouter：`PROVIDER=openrouter`，使用 `OPENROUTER_API_KEY`。

已有会话和图片历史无需迁移，切换只影响后续调用。修改配置后重启服务。
