# 历史技术说明

> 以下保留项目原有开发与配置细节，部分内容描述原开发环境；首次使用请以 [README](README.md) 为入口。文中提到的本机依赖、账号、安装包和运行数据不随开源仓库提供。

# 企业 AI 助手 · v0.1 开发版

员工通过钉钉单聊使用 AI；系统保存独立会话、关键词索引查询、图片版本和任务状态。已按 OpenAI 接口实现文本、生图和改图适配。默认运行模拟模式，不联网调用模型、不产生 API 费用。

**当前状态：本机已完成钉钉单聊端到端真实试用——文字、生图、上传图片、改图、图片版本链均有真实记录，投递全部成功。模型走 OpenRouter 真实计费调用。目前仅开通 1 名员工（不限次数测试账号）。管理能力目前由命令行和带鉴权的交互式 API 文档提供，尚未制作独立管理网页；PostgreSQL 与 Docker 部署路径仍未实测。**

**OpenRouter 更新：** 已支持通过 OpenRouter 调用 OpenAI 的文字、生图和改图模型。当前本机配置已选择 `PROVIDER=openrouter`，只需填写 `OPENROUTER_API_KEY`；钉钉配置保留。请优先阅读 [OpenRouter 接入说明](docs/OpenRouter接入.md)。

## 先体验

此工作区已经准备好 `.venv`、`.env` 和本地数据库。在项目目录打开 PowerShell：

```powershell
.\.venv\Scripts\python.exe -m app demo
```

依次输入：

```text
/新对话 培训通知
帮我写一份研发干部培训通知
/标签 培训,HR
/新对话 人才培养海报
/生图 蓝色科技风人才培养海报
/图片
/搜索 培训
/最近
```

用实际返回的编号操作：

```text
/继续 1
再简短一点
/记录 1
/改图 #1 把右下角留白
```

`exit` 退出。模拟回答和图片有明确标记，仅用于验证流程，不代表模型效果。演示记录位于 `data/demo/`，与真实员工数据库完全分开；下次启动仍能查询。

也可执行完整的自动演示：

```powershell
.\.venv\Scripts\python.exe -m app demo --scripted
```

## 功能

| 能力 | 实现 |
|---|---|
| 文字问答 | 排队执行、多轮上下文、长回复分段、错误反馈 |
| 会话管理 | 新建、恢复、最近列表翻页、归档；归档不删除历史 |
| 分类检索 | 标题、标签、消息正文关键词搜索；自动按任务类型分类 |
| 长对话 | 保存原始消息；按输入字节预算组装上下文，必要时生成摘要 |
| 生图与改图 | 每次一张；显式指定来源图片；记录父版本和修改要求 |
| 企业访问 | 校验钉钉企业、机器人、员工标识；员工白名单；首版仅单聊 |
| 用量限制 | 个人每日文本/图片次数、全局资源点、可配置的费用预留预算 |
| 可靠性 | 持久化任务、入库后 ACK、消息去重、模型与投递分离、重发已有结果 |
| 图片保留 | 默认保留 30 天；过期后保留版本元数据，文件不可再改图 |
| 管理 | 员工开通/停用/限额、任务状态、用量和失败投递查询 |

## 所有员工指令

| 指令 | 说明 |
|---|---|
| `/帮助` | 查看指令 |
| `/新对话 名称` | 创建新会话，旧记录保留；省略名称时取第一条任务内容命名 |
| `/最近 [页码]` | 每页 10 个非归档会话 |
| `/继续 会话编号` | 切换上下文；已归档会话会恢复 |
| `/归档 会话编号` | 从最近列表收起，仍可检索 |
| `/搜索 关键词` | 本人的标题、标签、原始消息搜索，含归档，最多 20 条 |
| `/记录 会话编号 [页码]` | 按问答顺序查看原文，每页 5 条消息 |
| `/标签 培训,周报` | 设置当前会话标签；不带参数清空标签 |
| `/分类 文字` | 筛选含文字任务的会话，也支持“生图”“改图” |
| `/生图 描述` | 创建一张图片 |
| `/改图 #图片编号 修改要求` | 修改当前会话中指定的图片 |
| `修改这张图，把背景改成夜晚` | 修改当前会话最新一张图片；有图片处理未结束时先等待 |
| 发送单张图片 | 保存为来源图并返回编号；也可同条图文输入 `/改图 修改要求` |
| `/图片` | 当前会话最近 20 张图片及父版本 |
| `/看图 图片编号` | 重新发送本人已有图片，不重新生成 |
| `/任务 任务编号` | 查询本人任务状态/结果 |
| `/额度` | 查询当天次数及全局资源预留 |

模型不读取钉钉完整聊天窗口。每个用户有一个当前 AI 会话，排队任务在提交时绑定该会话，后续切换不会把结果写到另一个会话。消息在上下文中按任务顺序排列，避免用户连续输入导致问答顺序错乱。

## 新电脑安装

Python 3.11 及以上；本地使用 SQLite，无需先装 Docker。正式部署配置使用 PostgreSQL。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe scripts\prepare_local.py
.\.venv\Scripts\python.exe -m app init
```

`prepare_local.py` 仅在 `.env` 不存在时创建它，并生成随机管理令牌和数据库密码；不会覆盖现有凭据。`.env`、数据库和图片目录均已排除在 Git 与 Docker 构建上下文之外。

## 接入 OpenAI 和钉钉

通过 OpenRouter 接入时，使用 [OpenRouter 接入说明](docs/OpenRouter接入.md) 中的独立配置；下文的 `TEXT_*` / `IMAGE_*` 模型地址、模型名与密钥是直连 OpenAI 配置。

详见 [接入与验收](docs/接入与验收.md)。建议先只开通自己。

本地 `.env` 已留好填写位置。初始文本模型为 `gpt-4.1-mini`，图片模型为 `gpt-image-2`，均可更换；是否能调用取决于你的 API 项目权限。真实调用模式使用 `PROVIDER=openai`，模拟模式使用 `PROVIDER=mock`。

原始接口参考：[GPT-4.1 Mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)、[GPT Image 2](https://developers.openai.com/api/docs/models/gpt-image-2)、[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[图片生成](https://developers.openai.com/api/reference/resources/images/methods/generate)、[图片编辑](https://developers.openai.com/api/reference/resources/images/methods/edit)。

## 管理操作

命令行管理具有服务器本机操作权限，不需要额外管理令牌：

```powershell
.\.venv\Scripts\python.exe -m app user --corp 企业ID --staff 员工ID --name 姓名 --text-limit 50 --image-limit 5
.\.venv\Scripts\python.exe -m app users
.\.venv\Scripts\python.exe -m app overview
.\.venv\Scripts\python.exe -m app costs --days 30
.\.venv\Scripts\python.exe -m app tasks
.\.venv\Scripts\python.exe -m app deliveries
```

`costs` 统计服务商**已返回的真实费用**，按日和按人分组，默认最近 7 天。它与 `overview` 里的资源点是两回事：资源点是调用前的额度预留，这里是实际花了多少钱。处理中、失败或未返回费用的任务不计入，所以是实际花费的下限，需与供应商账单核对。开通员工时建议填 `--name`，否则费用报表里只有一个工号。

停用员工：再次执行 `user` 命令并加上 `--disabled`。使用列表不展示员工聊天正文；服务器数据库本身仍包含原始聊天记录，应限制服务器/备份的访问权限。

管理 API：

```powershell
.\.venv\Scripts\python.exe -m app serve
```

打开 `http://127.0.0.1:8000/docs`，在 Authorize 中填入 `.env` 的 `ADMIN_TOKEN`。提供概览、用户与额度、费用统计、任务记录四组管理接口，其中费用统计为 `/admin/costs?days=7`。默认仅监听本机。Swagger 文档静态资源从公共 CDN 加载，文档页无权直接读取 `.env`；命令行管理不依赖 CDN。

### 失败处理

任务状态：`queued` 排队、`running` 执行、`succeeded` 已保存结果、`failed` 明确失败、`uncertain` 结果未知。

模型请求超时或执行进程中断后，不自动重试付费调用；未知任务不会进入后续模型的历史上下文。管理员需先核查供应商请求和账单，再决定是否由员工发起新任务。

投递状态与模型任务状态分别保存。`sent` 表示钉钉接口受理，不表示员工已读。钉钉网络异常可能已经送达，因此结果未知投递需要先检查聊天窗口再重发：

```powershell
.\.venv\Scripts\python.exe -m app resend 投递编号
```

重发读取原有文本/图片，不再次调用模型。

### 额度和费用

- 个人次数和全局资源点在任务入库时原子预留，包含排队任务，避免并发绕过。
- 默认文本任务 1 点、图片任务 20 点；每天按上海时区重置统计。
- 失败和结果未知任务保留额度占用，避免失败重试无限消费。
- `TEXT_RESERVE_FEN`、`IMAGE_RESERVE_FEN` 是管理员设置的单任务保守估算；文本值须涵盖可能发生的摘要和回答两次请求。
- `DAILY_BUDGET_FEN` 为估算预留上限；未填写价格时不显示为真实费用。应用资源点及估算预算不能替代供应商实际账单限制。
- 保存接口返回的 usage 元数据，文本摘要/回答分开记录。尚未实现按动态单价和汇率精确结算。
- 实际花费另有统计：`python -m app costs --days 30` 或 `GET /admin/costs`，按日和按人汇总服务商已返回的费用（美元）。资源点预留与实际花费应分开看，两者不一致属正常。

## 部署结构

```text
钉钉 → stream 进程 → 数据库里的消息回执 / 会话 / 排队任务
                          ↓
                 worker 进程 → OpenAI
                          ↓
                  结果 / 图片 / 待投递消息
                          ↓
                 sender 进程 → 钉钉

管理 CLI / API → 用户、额度、任务元数据
```

消息投递进程独立运行，生图不会阻塞后续消息的受理提示。v0.1 的模型工作进程为单实例串行队列，适合功能联调和小范围试用；高并发调度尚未实现。

### Docker Compose

准备 Docker 引擎和 `.env`（含随机字母数字 `POSTGRES_PASSWORD`、`ADMIN_TOKEN`，`DEV_TOKEN` 留空）：

```powershell
docker compose up --build -d
docker compose run --rm admin python -m app user --corp 企业ID --staff 员工ID --name 姓名
docker compose --profile dingtalk up -d
```

`init` 服务只创建初始表结构；数据库端口不映射到宿主机。管理 API 仅映射宿主机回环地址。worker、sender、stream 共用数据库及图片卷。**同一部署只运行一个 worker 和一个 sender，且必须共用数据目录中的文件锁；不支持跨主机多副本。**

当前环境没有运行中的 Docker 引擎，容器构建、PostgreSQL 实际运行和服务器网络仍需部署时验证；本地已验证的是 SQLite 路径。

## 数据保存与后续扩展

- 图片当前存储在服务器私有目录中，不通过公开静态链接暴露。对象存储接口是后续部署扩展，当前尚未接入。
- 默认图片保留 30 天，worker 每小时清理到期文件，排队/执行任务正在使用的来源图暂不清理。元数据和版本链继续保留。
- 原始聊天记录默认持续保存，归档不等于删除。员工自助删除、企业保留策略与审计删除流程待后续实现；试用前应明确记录范围。
- 摘要是压缩背景，可能有遗漏；特别长的历史片段会有截断标记，原文不变且可关键词搜索。尚未实现自动语义检索旧记录。
- 当前关键词查询为数据库字符串匹配，兼容中文；没有向量数据库或专业中文全文索引。
- 图片是栅格输出，不保证精确保留原文、Logo 或二维码；模板排版及固定元素叠加不在本版本。
- 表结构通过 `create_all` 初始化，尚无升级迁移工具；后续改变线上结构前需补迁移脚本。
- 备份时停止 stream/worker/sender/API，再备份整个 `data/`；容器部署同时备份 PostgreSQL 与图片卷。不要仅复制正在使用的 SQLite 主文件而遗漏 WAL。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app demo --scripted
.\.venv\Scripts\python.exe -m app check
```

测试覆盖用户隔离、跨会话访问、并发去重和限额、历史摘要与原文保留、图片来源绑定、异常恢复、重发不重复生成、过期文件、管理鉴权及接口契约。所有模型/钉钉接口契约测试使用 HTTP 模拟，不会产生真实调用。

### 真实试用规模（截至 2026-09-27 本机数据库）

| 项目 | 实测值 |
|---|---|
| 已开通员工 | 1 人（不限次数测试账号） |
| 钉钉收/发往返 | 50 个任务，126 条投递**全部 sent**，无投递失败 |
| 任务构成 | 文字 18、生图 8、改图 12 成功；上传 7 次成功 |
| 图片 | 30 张，含版本链 |
| 真实单价 | 改图约 $0.043/次，生图约 $0.032/次，意图识别约 $0.0003/次 |
| 测试 | 88 项通过 |

试用期间有 5 个失败任务（3 次上传、2 次连带改图），根因是钉钉返回的图片 OSS 域名不在 `DINGTALK_DOWNLOAD_HOSTS` 白名单内；同一会话稍后的上传均成功，属于域名轮换导致的间歇性问题，不是配置填错。现在失败信息会记录具体域名，便于直接补进白名单。这 5 条是测试残留，未做数据清理，也**未退还**其占用的资源点。

### 尚未验证

- 多员工并发使用：当前只有一个账号跑过。
- PostgreSQL 与 Docker：本机没有可用 Docker 引擎，从未实机运行。
- 服务器常开部署、备份恢复、账单与供应商对账。
- 管理界面：仍只有 CLI 和 Swagger。

## 源码目录

```text
app/config.py       配置与校验
app/db.py           用户、会话、消息、任务、图片、投递模型
app/service.py      指令、关键词检索、权限、额度预留
app/providers.py    模拟模型 / OpenAI 接口适配
app/worker.py       队列、摘要、恢复、投递与图片保留
app/dingtalk.py     Stream 回调、身份校验、钉钉文件与消息接口
app/admin.py        管理操作
app/api.py          带鉴权的管理 API、本地模拟接口
app/cli.py          演示、进程启动与管理命令
tests/              核心行为与接口契约测试
```
