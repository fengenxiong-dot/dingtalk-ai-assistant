from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from .db import Outbox, Task, User
from .service import UserError, today

# Text tasks report routing/summary/answer as separate calls; image and edit
# tasks report generation cost at the top level alongside a routing entry.
COST_ENTRIES = ("cost", "routing", "summary", "answer")
NO_MODEL_CALL = {"upload"}


def task_cost(usage):
    """Sum every provider-reported cost for one task; never use cost_details.

    Returns (total, reported). `reported` is False when the task reached a
    model but no cost came back, which makes the sum a lower bound.
    """
    total, reported = Decimal("0"), False
    if not isinstance(usage, dict):
        return total, reported
    for key in COST_ENTRIES:
        value = usage.get(key)
        if isinstance(value, dict):
            value = value.get("cost")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += Decimal(str(value))
            reported = True
    return total, reported


def overview(db, settings):
    with db.Session() as s:
        kinds = dict(s.execute(select(Task.kind, func.count()).where(Task.day == today()).group_by(Task.kind)).all())
        credits, reserved = s.execute(select(func.coalesce(func.sum(Task.credits), 0), func.coalesce(func.sum(Task.reserve_fen), 0)).where(Task.day == today())).one()
        spent = Decimal("0")
        for usage in s.scalars(select(Task.usage).where(Task.day == today())).all():
            spent += task_cost(usage)[0]
        return {"provider": settings.provider, "day": today(), "users": s.scalar(select(func.count()).select_from(User).where(User.enabled.is_(True))), "active_users_today": s.scalar(select(func.count(func.distinct(Task.user_id))).where(Task.day == today())), "tasks_by_kind": kinds, "credits_reserved": credits, "daily_credit_limit": settings.daily_credit_limit, "estimated_reserved_fen": reserved, "daily_budget_fen": settings.daily_budget_fen, "reported_cost_usd_today": str(spent), "currency_note": "credits_reserved 与 estimated_reserved_fen 是调用前的额度预留，不是实际账单；reported_cost_usd_today 是服务商已返回的费用合计，为实际花费下限。", "delivery_failures": s.scalar(select(func.count()).select_from(Outbox).where(Outbox.status.in_(["failed", "uncertain"]))) }


def costs(db, days=7, limit=50):
    """Actual provider-reported spend, grouped by day and by employee.

    Deliberately separate from the credit/point reservation in `overview`:
    points are a pre-flight cap, this is what OpenRouter actually charged.
    """
    days = max(1, min(int(days), 366))
    cutoff = (datetime.strptime(today(), "%Y-%m-%d") - timedelta(days=days - 1)).date().isoformat()
    with db.Session() as s:
        rows = s.execute(
            select(Task.id, Task.kind, Task.status, Task.day, Task.usage, Task.user_id, User.staff_id, User.name)
            .join(User, User.id == Task.user_id)
            .where(Task.day >= cutoff)
        ).all()
    by_day, by_user, by_kind = {}, {}, {}
    total, reported_tasks, unreported = Decimal("0"), 0, 0
    for row in rows:
        amount, reported = task_cost(row.usage)
        total += amount
        day = by_day.setdefault(row.day, {"day": row.day, "tasks": 0, "usd": Decimal("0"), "by_kind": {}})
        day["tasks"] += 1
        day["usd"] += amount
        day["by_kind"][row.kind] = day["by_kind"].get(row.kind, 0) + 1
        user = by_user.setdefault(row.user_id, {"user_id": row.user_id, "staff_id": row.staff_id, "name": row.name, "tasks": 0, "usd": Decimal("0")})
        user["tasks"] += 1
        user["usd"] += amount
        by_kind[row.kind] = by_kind.get(row.kind, Decimal("0")) + amount
        if reported:
            reported_tasks += 1
        elif row.kind not in NO_MODEL_CALL and row.status != "failed":
            unreported += 1
    return {
        "currency": "USD",
        "window": {"from": cutoff, "to": today(), "days": days},
        "note": "金额为服务商已返回的调用费用合计；处理中、失败或未返回费用的任务不计入，因此是实际花费的下限。应与供应商账单核对。",
        "totals": {
            "tasks": len(rows),
            "tasks_with_reported_cost": reported_tasks,
            "tasks_without_reported_cost": unreported,
            "usd": str(total),
            "by_kind": {k: str(v) for k, v in sorted(by_kind.items())},
        },
        "by_day": [
            {"day": d["day"], "tasks": d["tasks"], "usd": str(d["usd"]), "by_kind": d["by_kind"]}
            for d in sorted(by_day.values(), key=lambda x: x["day"], reverse=True)
        ],
        "by_user": [
            {"user_id": u["user_id"], "staff_id": u["staff_id"], "name": u["name"], "tasks": u["tasks"], "usd": str(u["usd"])}
            for u in sorted(by_user.values(), key=lambda x: x["usd"], reverse=True)[:limit]
        ],
    }


def users(db):
    with db.Session() as s:
        return [{k: getattr(u,k) for k in ("id", "corp_id", "staff_id", "name", "enabled", "text_limit", "image_limit")} for u in s.scalars(select(User).order_by(User.id))]


def tasks(db, page=1):
    with db.Session() as s:
        # No prompt, result or session contents in ordinary admin views.
        return [{k: getattr(t,k) for k in ("id", "user_id", "conversation_id", "kind", "status", "error", "usage", "credits", "reserve_fen", "created_at", "finished_at")} for t in s.scalars(select(Task).order_by(Task.id.desc()).offset((page-1)*50).limit(50))]


def deliveries(db):
    with db.Session() as s:
        return [{k: getattr(o,k) for k in ("id", "user_id", "task_id", "status", "error")} for o in s.scalars(select(Outbox).where(Outbox.status.in_(["failed", "uncertain"])).order_by(Outbox.id).limit(100))]


def resend(db, delivery_id):
    with db.write() as s:
        item = s.get(Outbox, delivery_id)
        if not item or item.status not in {"failed", "uncertain"}:
            raise UserError("仅可重发失败或结果未知的投递。请先检查钉钉会话是否已经收到。")
        if not s.get(User, item.user_id).enabled:
            raise UserError("用户已停用，不能重发。")
        item.status, item.error = "pending", ""
        return {"text": "已排队重发原有结果，不会再次调用模型。"}

