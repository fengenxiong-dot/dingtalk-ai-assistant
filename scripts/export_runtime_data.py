"""导出运行数据，交给外部 AI 排查问题。

生成内容：
- assistant.db    含 WAL 内容的 SQLite 副本；钉钉下载凭据已清空
- *.jsonl         用户、会话、消息、任务、素材、投递的结构化记录
- 对话记录.md      按会话与时间顺序排列的可读记录
- 问题清单.md      失败任务与未成功投递的消息
- images/         素材图片副本，文件名前缀为素材编号

只读源库，不修改线上数据。
"""
import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


CN = timezone(timedelta(hours=8))


def parse(value):
    """库里存的是不带时区的 UTC 时间。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iso(value):
    stamp = parse(value)
    return stamp.isoformat().replace("+00:00", "Z") if stamp else None


def local(value):
    stamp = parse(value)
    return stamp.astimezone(CN).strftime("%Y-%m-%d %H:%M:%S") if stamp else ""


def query(session, sql, params=()):
    session.row_factory = sqlite3.Row
    return [dict(row) for row in session.execute(sql, params)]


def jsonl(path, name, records):
    with (path / name).open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def as_json(value):
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {"raw": str(value)}


def seconds_between(start, end):
    first, second = parse(start), parse(end)
    return round((second - first).total_seconds(), 3) if first and second else None


def task_line(task):
    label = {"text": "文字", "image": "生图", "edit": "改图", "upload": "上传"}.get(task["kind"], task["kind"])
    parts = [f"任务 #{task['id']}", label, task["status"]]
    duration = seconds_between(task["created_at"], task["finished_at"])
    if duration is not None:
        parts.append(f"耗时 {duration}s")
    if task["error"]:
        parts.append(task["error"])
    return " · ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="导出运行数据用于外部排查")
    parser.add_argument("--db", default="data/assistant.db", help="源 SQLite 文件")
    parser.add_argument("--assets", default="data/assets", help="素材图片目录")
    parser.add_argument("--out", default="handoff-data", help="输出目录")
    args = parser.parse_args()
    source = Path(args.db)
    if not source.exists():
        raise SystemExit(f"找不到数据库：{source}")
    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)

    # 先做一致性副本，避免读到正在写入的 WAL。
    live = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    copy_path = out / "assistant.db"
    copy = sqlite3.connect(str(copy_path))
    live.backup(copy)
    # 下载凭据可以在有效期内取回原图，不随包外发。
    copy.execute("UPDATE assets SET download_code = ''")
    copy.commit()
    copy.close()
    live.close()

    session = sqlite3.connect(f"file:{copy_path.as_posix()}?mode=ro", uri=True)
    users = query(session, "SELECT * FROM users ORDER BY id")
    conversations = query(session, "SELECT * FROM conversations ORDER BY id")
    messages = query(session, "SELECT * FROM messages ORDER BY id")
    tasks = query(session, "SELECT * FROM tasks ORDER BY id")
    assets = query(session, "SELECT * FROM assets ORDER BY id")
    outbox = query(session, "SELECT * FROM outbox ORDER BY id")

    for row in conversations:
        row["created_at_utc"], row["created_at_local"] = iso(row["created_at"]), local(row["created_at"])
        row["updated_at_utc"], row["updated_at_local"] = iso(row["updated_at"]), local(row["updated_at"])
    for row in messages:
        row["created_at_utc"], row["created_at_local"] = iso(row["created_at"]), local(row["created_at"])
    for row in tasks:
        row["created_at_utc"], row["created_at_local"] = iso(row["created_at"]), local(row["created_at"])
        row["finished_at_utc"], row["finished_at_local"] = iso(row["finished_at"]), local(row["finished_at"])
        row["duration_seconds"] = seconds_between(row["created_at"], row["finished_at"])
        row["usage"] = as_json(row["usage"])
    for row in assets:
        row["created_at_utc"], row["created_at_local"] = iso(row["created_at"]), local(row["created_at"])

    jsonl(out, "users.jsonl", users)
    jsonl(out, "conversations.jsonl", conversations)
    jsonl(out, "messages.jsonl", messages)
    jsonl(out, "tasks.jsonl", tasks)
    jsonl(out, "assets.jsonl", assets)
    jsonl(out, "outbox.jsonl", outbox)

    task_index = {row["id"]: row for row in tasks}
    asset_index = {row["id"]: row for row in assets}
    by_conversation = {}
    for row in messages:
        by_conversation.setdefault(row["conversation_id"], []).append(row)

    lines = ["# 对话记录（时间戳为 +08:00 本地时间）", ""]
    for conversation in conversations:
        lines += [f"## 会话 #{conversation['id']} · {conversation['title']}", ""]
        lines.append(f"- 标签：{conversation['tags'] or '无'}")
        lines.append(f"- 状态：{'已归档' if conversation['archived'] else '活动'}")
        lines.append(f"- 创建：{conversation['created_at_local']}　最后更新：{conversation['updated_at_local']}")
        if conversation["summary"]:
            lines.append(f"- 历史摘要：{conversation['summary']}")
        lines.append("")
        for message in by_conversation.get(conversation["id"], []):
            task = task_index.get(message["task_id"])
            who = "用户" if message["role"] == "user" else "助手"
            header = f"### {message['created_at_local']} · {who}"
            if task:
                header += f"（{task_line(task)}）"
            body = message["content"].strip() or "（无文字内容）"
            lines += [header, "", body, ""]
            if message["role"] == "assistant" and task and task["result_asset_id"]:
                asset = asset_index.get(task["result_asset_id"])
                if asset:
                    lines.append(f"（图片：{asset['path'] or '已过期'}，素材 #{asset['id']}）")
                    lines.append("")
    (out / "对话记录.md").write_text("\n".join(lines), encoding="utf-8")

    problems = ["# 问题清单", ""]
    failures = [row for row in tasks if row["status"] in {"failed", "uncertain"}]
    undelivered = [row for row in outbox if row["status"] != "sent"]
    problems.append(f"- 失败或结果未知的任务：{len(failures)} 条")
    problems.append(f"- 未确认送达的投递：{len(undelivered)} 条")
    problems.append("")
    for row in failures:
        problems += [f"## 任务 #{row['id']} · {row['kind']} · {row['status']}", ""]
        problems.append(f"- 时间：{row['created_at_local']}（耗时 {row['duration_seconds']}s）")
        problems.append(f"- 错误：{row['error']}")
        problems.append(f"- 用户输入：{(row['prompt'] or '').strip()}")
        problems.append("")
    for row in undelivered:
        problems += [f"## 投递 #{row['id']} · {row['status']}", ""]
        problems.append(f"- 关联任务：{row['task_id']}")
        problems.append(f"- 错误：{row['error'] or '无'}")
        problems.append(f"- 内容：{(row['text'] or '').strip()[:200]}")
        problems.append("")
    (out / "问题清单.md").write_text("\n".join(problems), encoding="utf-8")

    asset_root = Path(args.assets)
    copied, missing = [], []
    for row in assets:
        if not row["path"]:
            continue
        origin = asset_root / Path(row["path"]).name
        if not origin.exists():
            missing.append(row["id"])
            continue
        target = out / "images" / f"asset{row['id']}_{origin.name}"
        shutil.copy2(origin, target)
        sidecar = origin.with_suffix(".json")
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_suffix(".json"))
        copied.append(target.name)
    manifest = [{"asset_id": row["id"], "file": f"asset{row['id']}_{Path(row['path']).name}", "prompt": row["prompt"], "parent_id": row["parent_id"], "created_at_local": row["created_at_local"]} for row in assets if row["path"]]
    jsonl(out, "images-manifest.jsonl", manifest)
    session.close()

    size = sum(item.stat().st_size for item in out.rglob("*") if item.is_file())
    print(json.dumps({
        "out": str(out),
        "conversations": len(conversations),
        "messages": len(messages),
        "tasks": len(tasks),
        "assets": len(assets),
        "outbox": len(outbox),
        "images_copied": len(copied),
        "images_missing": missing,
        "total_mb": round(size / 1024 / 1024, 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
