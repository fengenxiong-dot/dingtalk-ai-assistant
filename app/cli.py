import argparse
import json
import logging
import sys
import uuid
from dataclasses import replace
from pathlib import Path

from filelock import FileLock
from sqlalchemy import select

from . import admin
from .config import Settings
from .db import Asset, Database, Task
from .service import Service, UserError
from .storage import Storage
from .worker import Worker


def show(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def demo(settings, scripted=False):
    # Always isolate the demo from real staff data and real provider credentials.
    directory = settings.data_dir / "demo"
    local = replace(settings, provider="mock", data_dir=directory, database_url=f"sqlite:///{directory.as_posix()}/demo.db", daily_credit_limit=100000, daily_budget_fen=0)
    db = Database(local)
    db.init()
    service, worker = Service(db, local), Worker(db, local)
    service.provision("local", "demo", "演示用户", 10000, 10000)
    print("企业 AI 本地演示 · 模拟模型 · 不消耗 API 费用")
    print("输入 /帮助 查看指令，输入 exit 退出。")

    def execute(value):
        print(f"\n你：{value}")
        result = service.receive("local", "demo", uuid.uuid4().hex, value)
        print(result["text"])
        if "task_id" in result:
            while worker.run_once():
                pass
            with db.Session() as s:
                task = s.get(Task, result["task_id"])
                print(task.result or task.error)
                if task.result_asset_id:
                    asset = s.get(Asset, task.result_asset_id)
                    print("图片文件：", Storage(local).resolve(asset.path))
        return result

    with FileLock(str(directory / "worker.lock"), timeout=0):
        worker.recover()
        if scripted:
            c = execute("/新对话 培训通知")["conversation_id"]
            execute("请写一份研发干部培训通知")
            execute("/标签 培训,HR")
            execute("/新对话 人才海报")
            generated = execute("/生图 蓝色科技风人才培养海报")
            with db.Session() as s:
                asset_id = s.get(Task, generated["task_id"]).result_asset_id
            execute(f"/改图 #{asset_id} 右下角留白")
            execute("/图片")
            execute("/搜索 培训")
            execute(f"/继续 {c}")
            execute("再简短一点")
            execute("/额度")
        else:
            while True:
                try:
                    value = input("\n你 > ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\n已退出。记录已保存，下次启动仍可查询。")
                    break
                if value.lower() in {"exit", "quit", "退出"}:
                    break
                if value:
                    try:
                        execute(value)
                    except UserError as exc:
                        print(str(exc))


def main():
    # Consistent Chinese output in Windows terminals and redirected tool output.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="企业 AI 助手")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="初始化数据库；不会创建任何有权限的真实用户")
    d = sub.add_parser("demo", help="运行独立的离线模拟演示")
    d.add_argument("--scripted", action="store_true")
    p = sub.add_parser("user", help="开通或调整员工权限")
    p.add_argument("--corp", required=True)
    p.add_argument("--staff", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--text-limit", type=int)
    p.add_argument("--image-limit", type=int)
    p.add_argument("--disabled", action="store_true")
    sub.add_parser("worker", help="运行后台模型任务与图片下载进程")
    sub.add_parser("sender", help="独立投递钉钉消息，避免生图阻塞受理提示")
    sub.add_parser("stream", help="连接钉钉 Stream，凭据从 .env 读取")
    p = sub.add_parser("serve", help="运行本地管理 API 和交互式接口文档")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    for command in ("overview", "users", "tasks", "deliveries"):
        sub.add_parser(command)
    p = sub.add_parser("costs", help="按日和按人统计服务商已返回的真实费用")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("resend", help="核查钉钉会话后重发原有结果，不会重新调用模型")
    p.add_argument("delivery_id", type=int)
    sub.add_parser("check", help="只检查配置是否齐全，不联网、不打印凭据")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.load(require_credentials=args.command not in {"check", "demo"})
    if args.command == "demo":
        demo(settings, args.scripted)
        return
    if args.command == "check":
        router = settings.provider == "openrouter"
        show({"provider": settings.provider,
              "text_model": settings.openrouter_text_model if router else settings.text_model,
              "image_model": settings.openrouter_image_model if router else settings.image_model,
              "model_credentials_configured": bool(settings.openrouter_api_key.strip()) if router else bool(settings.text_api_key and settings.image_api_key),
              "dingtalk_configured": all(getattr(settings,k) for k in ("dingtalk_corp_id", "dingtalk_client_id", "dingtalk_client_secret", "dingtalk_robot_code")),
              "admin_token_configured": bool(settings.admin_token), "note": "此检查不验证账号权限或网络可用性；模拟模式不需要模型密钥"})
        return
    db = Database(settings)
    service = Service(db, settings)
    if args.command == "init":
        db.init()
        print("数据库已初始化；未自动开通员工。")
    elif args.command == "user":
        show({"user_id": service.provision(args.corp, args.staff, args.name, args.text_limit, args.image_limit, not args.disabled)})
    elif args.command in {"overview", "users", "tasks", "deliveries", "costs"}:
        if args.command == "overview":
            show(admin.overview(db, settings))
        elif args.command == "costs":
            show(admin.costs(db, args.days))
        else:
            show(getattr(admin, args.command)(db))
    elif args.command == "resend":
        show(admin.resend(db, args.delivery_id))
    elif args.command in {"worker", "sender"}:
        from .dingtalk import DingTalkTransport
        transport = DingTalkTransport(settings) if settings.dingtalk_client_id else None
        worker = Worker(db, settings, transport=transport)
        worker.run_forever() if args.command == "worker" else worker.run_sender()
    elif args.command == "stream":
        from .dingtalk import run_stream
        run_stream(service, settings)
    elif args.command == "serve":
        import uvicorn
        from .api import create_app
        if args.host not in {"127.0.0.1", "localhost", "::1"} and (not settings.admin_token or settings.dev_token):
            raise ValueError("非本机监听须设置管理员令牌并关闭模拟 HTTP 入口。")
        uvicorn.run(create_app(settings, db), host=args.host, port=args.port)
