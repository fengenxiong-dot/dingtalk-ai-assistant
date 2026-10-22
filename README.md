# 钉钉企业 AI 助手

让员工在熟悉的钉钉单聊入口中使用 AI，并管理会话、图片任务与用量。

[项目主页](https://github.com/fengenxiong-dot/dingtalk-ai-assistant) · [作者与项目合集](https://github.com/fengenxiong-dot) · [许可证](LICENSE)

## 为什么做这个工具

HR 和内部运营经常要写通知、整理思路、准备培训素材，也会遇到同事不知道如何开始使用 AI 的问题。这个项目尝试把 AI 能力接到企业已有的沟通入口，降低使用门槛，并提供会话隔离、任务状态和用量管理。

## 核心功能

- 钉钉单聊文字问答，支持多轮会话。
- 新建、恢复、归档会话，使用标签与关键词查找历史。
- 生成或修改图片，记录图片来源与版本关系。
- 员工白名单、个人次数限制和预算预留相关管理。
- 持久化任务、消息去重、生成与消息投递分离。
- 命令行与带鉴权的 API 用于管理用户、任务、投递和用量。
- 提供本地模拟模式，不必先配置真实模型或钉钉应用即可了解流程。

## 适合哪些场景

- 培训通知、活动文案和课程海报草稿辅助。
- 企业内小范围 AI 使用试点。
- 需要在熟悉的工作入口中保留会话并管理调用额度。

## 第一次使用

### 准备环境

Python 3.11 或以上。先体验模拟模式可使用 SQLite；真实接入需要自己的钉钉企业应用及模型服务账号。

下载方式：会使用 Git 可按下面的命令克隆；也可在本页点击 **Code → Download ZIP**，解压后在项目目录打开终端，跳过 `git clone` 和 `cd` 两步。

### 安装与启动

```powershell
git clone https://github.com/fengenxiong-dot/dingtalk-ai-assistant.git
cd dingtalk-ai-assistant
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe scripts\prepare_local.py
.\.venv\Scripts\python.exe -m app init
.\.venv\Scripts\python.exe -m app demo
```

上面是 Windows PowerShell 示例；macOS/Linux 可将虚拟环境 Python 路径替换为 `.venv/bin/python`。

### 操作流程

1. 先运行模拟演示，输入 `/新对话 培训通知`，再输入要起草的通知需求。
2. 尝试 `/最近`、`/搜索 培训` 和 `/图片` 等指令，了解会话与任务流程。
3. 需要真实模型时，按 [OpenRouter 接入说明](docs/OpenRouter接入.md) 配置自己的环境变量。
4. 需要钉钉接入时，按 [接入与验收](docs/接入与验收.md) 配置企业应用，并只开通测试账号。
5. 按技术指南分别启动 Stream、worker 和 sender，再检查实际消息投递及费用。

## 当前状态与限制

默认模拟流程不代表真实模型效果。当前首版以单聊和小范围试用为主，模型任务执行并发有限；没有独立管理员网页。Docker/PostgreSQL 的部署路径尚未完成本次开源环境验收。调用真实模型会产生服务商费用，费用预留不能代替供应商账单上限。

**验证情况：** 本次完成 Python 语法检查，未重新运行真实钉钉与模型联调。

## 数据保存与配置

会话、任务及图片数据位于配置的数据库和 `data/`。真实模型模式会把相应文字或图片发送给你配置的服务商。

公开仓库只提供源码与示例配置。真实人员资料、业务文件、数据库和 API Key 需要由使用者自行管理。先用虚构数据走通流程，确认符合自己的需求后再投入实际工作。

## 关于作者

我是人力资源行业从业者，从完全零基础、零代码起步，通过 Web Coding 与 AI 辅助开发，把工作中的需求逐步做成工具。我主要负责场景梳理、需求描述、业务流程、试用反馈和迭代；代码实现借助 AI 完成。这些项目记录的是业务人员解决实际问题的实践过程。

如果你也在人力资源、培训或日常办公中遇到类似问题，欢迎通过本仓库 Issues 描述场景、复现问题或提出改进建议，请使用虚构示例。

## 更多说明

- [历史技术说明](TECHNICAL_GUIDE.md)：保留原有配置、功能细节和开发记录，注意其中的环境差异。
- [初次开源发布说明](OPEN_SOURCE_RELEASE.md)
- [第三方依赖说明](THIRD_PARTY_NOTICES.md)

## 许可证

本项目源码采用 **MIT**，见 [LICENSE](LICENSE)。第三方依赖保留各自许可证。
