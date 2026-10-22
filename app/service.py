import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from .db import Asset, Conversation, Message, Outbox, Receipt, Task, User, utcnow
from .message_format import chunks

HELP = """企业 AI 助手
直接输入文字：问答、写作、翻译。
生图可直接说「画一只猫」或「生成一张图片：描述」，也可用 /生图 描述。
/新对话 名称 · /最近 [页码] · /继续 会话编号
/搜索 关键词 · /记录 会话编号 [页码]
/标签 培训,周报 · /归档 会话编号
/分类 文字|生图|改图 · /生图 描述
修改最近一张图片：修改这张图，把背景改成夜晚
修改更早的图片：/改图 #图片编号 修改要求
/图片 · /看图 图片编号 · /任务 任务编号 · /额度
/费用 [任务编号]：查看最近任务或指定任务的美元费用拆项
旧记录不会因新对话而删除。"""


class UserError(Exception):
    pass


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def number(value):
    value = value.lstrip("#")
    if not re.fullmatch(r"[0-9]{1,15}", value) or int(value) < 1:
        raise UserError("请填写有效编号，例如 /继续 23。")
    return int(value)


def is_image_request(text):
    """Recognize explicit drawing requests; leave prose/code requests as text."""
    request = re.sub(r"^(?:请\s*)?(?:(?:帮我|给我|为我)\s*)?", "", text).strip()
    request = request.split("\n", 1)[0]
    request = re.sub(r"^(?:根据|依据).{0,20}?[，,:：]\s*", "", request)
    if re.search(r"提示词|关键词|故事|教程|代码|脚本|表格|文章|如何|怎么", request):
        return False
    return bool(re.match(
        r"^(?:画(?:一|个|只|张|幅)|绘制|生图|生成\s*(?:一)?(?:张|幅|只)|生成(?:图片|图像|插画|照片))",
        request,
    ))


class Service:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def provision(self, corp_id, staff_id, name="", text_limit=None, image_limit=None, enabled=True):
        if not corp_id or not staff_id or len(corp_id) > 128 or len(staff_id) > 128:
            raise UserError("企业和员工 ID 必须为 1–128 个字符")
        if any(v is not None and v < 0 for v in (text_limit, image_limit)):
            raise UserError("额度不能为负")
        with self.db.write() as s:
            user = s.scalar(select(User).where(User.corp_id == corp_id, User.staff_id == staff_id))
            if user is None:
                user = User(corp_id=corp_id, staff_id=staff_id, text_limit=self.settings.default_text_limit, image_limit=self.settings.default_image_limit)
                s.add(user)
            user.name, user.enabled = name[:128], enabled
            if text_limit is not None:
                user.text_limit = text_limit
            if image_limit is not None:
                user.image_limit = image_limit
            s.flush()
            return user.id

    @staticmethod
    def conversation(s, user, conversation_id):
        item = s.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id))
        if item is None:
            raise UserError("找不到该会话，或你没有访问权限。")
        return item

    def active(self, s, user):
        if user.active_id:
            item = self.conversation(s, user, user.active_id)
            if not item.archived:
                return item
        item = Conversation(user_id=user.id, title="新对话")
        s.add(item)
        s.flush()
        user.active_id = item.id
        return item

    @staticmethod
    def send(s, user_id, text, channel, task_id=None, asset_id=None):
        if channel != "dingtalk":
            return
        # DingTalk text size limits apply to encoded bytes; keep each piece small.
        for value in chunks(text):
            s.add(Outbox(user_id=user_id, task_id=task_id, text=value))
        if asset_id:
            s.add(Outbox(user_id=user_id, task_id=task_id, text="", asset_id=asset_id))

    def receive(self, corp_id, staff_id, message_id, text, channel="local", download_code="", rejection=""):
        if not message_id or len(message_id) > 512:
            raise UserError("缺少或无效的消息 ID")
        if len(text.encode("utf-8")) > min(10000, self.settings.context_byte_budget // 2):
            raise UserError("这条消息太长，请分段发送或缩短内容。")
        key = hashlib.sha256(f"{channel}\0{corp_id}\0{staff_id}\0{message_id}".encode()).hexdigest()
        with self.db.write() as s:
            user = s.scalar(select(User).where(User.corp_id == corp_id, User.staff_id == staff_id, User.enabled.is_(True)))
            if not user:
                raise UserError("你尚未开通试用权限，请联系管理员。")
            old = s.get(Receipt, key)
            if old:
                return {**old.result, "duplicate": True}
            try:
                # Roll back any partial asset/session/quota mutation on invalid input.
                with s.begin_nested():
                    if rejection:
                        raise UserError(rejection)
                    result = self._handle(s, user, text.strip(), channel, download_code)
            except UserError as exc:
                result = {"text": str(exc)}
            s.add(Receipt(key=key, result=result))
            if not result.get("silent"):
                self.send(s, user.id, result["text"], channel, asset_id=result.get("send_asset_id"))
            return result

    def _handle(self, s, user, text, channel, download_code):
        edit_request = re.fullmatch(r"(?:请)?修改这张图[，,:：\s]*(.+)", text, re.S)
        if edit_request:
            text = "/改图 " + edit_request[1].strip()
        elif not text.startswith("/") and "logo" in text.lower() and re.search(r"替换|换成|更换", text):
            text = "/改图 " + text
        if download_code:
            if text and not text.startswith("/改图") and re.search(r"替换|换成|更换", text):
                text = "/改图 " + text
            if text and not text.startswith("/改图"):
                raise UserError("图片已识别。请单独发送图片，或在同一条图文消息中写 /改图 修改要求。")
            conversation = self.active(s, user)
            # Download promptly via worker before the temporary DingTalk code expires.
            pending = s.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id, Task.kind == "upload", Task.day == today()))
            if pending >= 30 and not self.unlimited(user):
                raise UserError("今日图片上传已达 30 张上限。")
            asset = Asset(user_id=user.id, conversation_id=conversation.id, download_code=download_code)
            s.add(asset)
            s.flush()
            upload = Task(user_id=user.id, conversation_id=conversation.id, kind="upload", prompt="上传图片", source_asset_id=asset.id, day=today(), channel=channel)
            s.add(upload)
            s.flush()
            if not text:
                return {"text": "收到图片，正在保存。", "task_id": upload.id, "asset_id": asset.id, "silent": True}
            prompt = text.removeprefix("/改图").strip()
            if not prompt:
                raise UserError("请在 /改图 后填写修改要求。")
            if re.search(r"替换|换成|更换", prompt):
                target = s.scalar(select(Asset).where(Asset.user_id == user.id, Asset.conversation_id == conversation.id, Asset.id != asset.id, Asset.path != "", Asset.expired.is_(False)).order_by(Asset.id.desc()).limit(1))
                if not target:
                    raise UserError("请先生成或上传要修改的海报，再发送新素材和替换要求。")
                result = self.enqueue(s, user, conversation, "edit", prompt, channel, target.id)
                s.get(Task, result["task_id"]).usage = {"reference_asset_ids": [asset.id], "upload_task_id": upload.id}
                return result
            return self.enqueue(s, user, conversation, "edit", prompt, channel, asset.id)

        cmd, _, argument = text.partition(" ")
        # Accept newlines and full-width whitespace after a command.
        if text.startswith("/"):
            pieces = text.split(maxsplit=1)
            cmd, argument = pieces[0], pieces[1].strip() if len(pieces) > 1 else ""
        if cmd in {"/帮助", "/help", "/start"} or not text:
            return {"text": HELP}
        if cmd == "/新对话":
            conversation = Conversation(user_id=user.id, title=argument[:120] or "新对话")
            s.add(conversation)
            s.flush()
            user.active_id = conversation.id
            return {"text": f"已切换到「{conversation.title}」#{conversation.id}。旧会话仍可搜索。", "conversation_id": conversation.id}
        if cmd == "/最近":
            page = number(argument) if argument else 1
            rows = s.scalars(select(Conversation).where(Conversation.user_id == user.id, Conversation.archived.is_(False)).order_by(Conversation.updated_at.desc(), Conversation.id.desc()).offset((page-1)*10).limit(10)).all()
            lines = []
            for c in rows:
                kinds = s.scalars(select(Task.kind).where(Task.conversation_id == c.id).distinct()).all()
                types = "/".join({"text":"文字", "image":"生图", "edit":"改图", "upload":"图片"}[k] for k in sorted(kinds))
                lines.append(f"{'当前 ' if c.id == user.active_id else ''}#{c.id} {c.title} [{c.tags or '未加标签'}] {types}")
            return {"text": f"最近会话 · 第 {page} 页\n" + ("\n".join(lines) or "暂无会话") + "\n使用 /继续 编号 恢复，/最近 页码 翻页。"}
        if cmd == "/分类":
            kind = {"文字":"text", "生图":"image", "改图":"edit"}.get(argument)
            if not kind:
                raise UserError("用法：/分类 文字 或 /分类 生图 或 /分类 改图")
            exists = select(Task.id).where(Task.conversation_id == Conversation.id, Task.kind == kind).exists()
            rows = s.scalars(select(Conversation).where(Conversation.user_id == user.id, exists).order_by(Conversation.updated_at.desc()).limit(20)).all()
            return {"text": "\n".join(f"#{c.id} {c.title}{'（归档）' if c.archived else ''}" for c in rows) or "没有此类型会话。"}
        if cmd in {"/继续", "/归档"}:
            conversation = self.conversation(s, user, number(argument))
            conversation.archived = cmd == "/归档"
            if cmd == "/继续":
                user.active_id = conversation.id
                conversation.updated_at = utcnow()
            elif user.active_id == conversation.id:
                user.active_id = None
            return {"text": f"已{'归档' if conversation.archived else '切换到'}「{conversation.title}」#{conversation.id}。", "conversation_id": conversation.id}
        if cmd == "/标签":
            conversation = self.active(s, user)
            conversation.tags = ",".join(dict.fromkeys(x.strip() for x in re.split("[,，]", argument) if x.strip()))[:300]
            return {"text": f"「{conversation.title}」标签已更新：{conversation.tags or '无'}"}
        if cmd == "/搜索":
            if not argument:
                raise UserError("用法：/搜索 培训（搜索本人标题、标签、原始消息，包含归档）")
            match = select(Message.id).where(Message.conversation_id == Conversation.id, Message.content.contains(argument, autoescape=True)).exists()
            rows = s.scalars(select(Conversation).where(Conversation.user_id == user.id, or_(Conversation.title.contains(argument, autoescape=True), Conversation.tags.contains(argument, autoescape=True), match)).order_by(Conversation.updated_at.desc()).limit(20)).all()
            lines = []
            for c in rows:
                msg = s.scalar(select(Message).where(Message.conversation_id == c.id, Message.content.contains(argument, autoescape=True)).order_by(Message.id.desc()).limit(1))
                snippet = ""
                if msg:
                    pos = msg.content.lower().find(argument.lower())
                    snippet = msg.content[max(0, pos-25):max(0, pos-25)+120].replace("\n", " ")
                lines.append(f"#{c.id} {c.title}{'（归档）' if c.archived else ''}\n{snippet}")
            return {"text": ("\n".join(lines) or "未找到匹配记录。") + "\n最多显示 20 条；/记录 编号 查看原文，/继续 编号 恢复。"}
        if cmd == "/记录":
            parts = argument.split()
            if not parts:
                raise UserError("用法：/记录 会话编号 [页码]")
            c = self.conversation(s, user, number(parts[0]))
            page = number(parts[1]) if len(parts) > 1 else 1
            rows = s.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.task_id, Message.id).offset((page-1)*5).limit(5)).all()
            return {"text": f"「{c.title}」第 {page} 页\n" + ("\n\n".join(f"{'你' if m.role == 'user' else '助手'}（任务 #{m.task_id}）：{m.content}" for m in rows) or "没有更多记录。")}
        if cmd == "/额度":
            texts, images, credits, fen = self.usage(s, user)
            if self.unlimited(user):
                return {"text": f"你的账号已开启不限次数测试。今日文字 {texts} 次，图片 {images} 次。模型调用仍按服务商实际用量计费。"}
            return {"text": f"今日文本 {texts}/{user.text_limit} 次；图片 {images}/{user.image_limit} 次。\n全体资源点 {credits}/{self.settings.daily_credit_limit}。含排队和结果未知任务。\n费用预留估算 {fen/100:.2f} 元，实际以供应商账单为准。"}
        if cmd == "/费用":
            query = select(Task).where(Task.user_id == user.id)
            if argument:
                query = query.where(Task.id == number(argument))
            task = s.scalar(query.order_by(Task.id.desc()).limit(1))
            if not task:
                raise UserError("没有找到你的任务记录。")
            usage = task.usage or {}
            parts = [("意图识别", usage.get("routing")), ("历史摘要", usage.get("summary")), ("文字回答", usage.get("answer"))]
            if task.kind in {"image", "edit"}:
                parts.append(("图片生成/修改", usage if "cost" in usage else None))
            from decimal import Decimal
            total, lines = Decimal("0"), []
            for label, value in parts:
                if not isinstance(value, dict):
                    continue
                cost = value.get("cost")
                if cost is None:
                    lines.append(label + "：费用未返回")
                else:
                    amount = Decimal(str(cost))
                    total += amount
                    lines.append(f"{label}：${amount:.6f}")
            return {"text": f"任务 #{task.id} · {task.status}\n" + ("\n".join(lines) if lines else "暂无模型费用记录。") + f"\n已记录合计：${total:.6f} 美元\n仅统计服务商已返回费用，处理中或失败任务可能不完整；最终以账单为准。"}
        if cmd == "/任务":
            task = s.scalar(select(Task).where(Task.id == number(argument), Task.user_id == user.id))
            if not task:
                raise UserError("找不到该任务，或你没有访问权限。")
            status = {"queued":"排队中", "running":"处理中", "succeeded":"已完成", "failed":"失败", "uncertain":"结果待核查"}.get(task.status, task.status)
            return {"text": f"任务 #{task.id} / 会话 #{task.conversation_id}：{status}\n{task.error or task.result or '尚无结果'}"}
        if cmd == "/图片":
            c = self.active(s, user)
            rows = s.scalars(select(Asset).where(Asset.conversation_id == c.id, Asset.user_id == user.id).order_by(Asset.id.desc()).limit(20)).all()
            return {"text": "\n".join(f"图片 #{a.id} ← {'图片 #'+str(a.parent_id) if a.parent_id else '原始版本'} · {'已过期' if a.expired else '可用' if a.path else '保存中'} · {a.prompt[:50]}" for a in rows) or "当前会话没有图片。"}
        if cmd == "/看图":
            asset = s.scalar(select(Asset).where(Asset.id == number(argument), Asset.user_id == user.id))
            if not asset or asset.expired or not asset.path:
                raise UserError("图片不存在、尚未保存或已过期。")
            return {"text": f"图片 #{asset.id} · 所属会话 #{asset.conversation_id}", "send_asset_id": asset.id}
        conversation = self.active(s, user)
        if cmd == "/生图":
            return self.enqueue(s, user, conversation, "image", argument, channel)
        if cmd == "/改图":
            if not re.match(r"#?\d+\s", argument) and re.search(r"替换|换成|更换", argument) and "logo" in argument.lower():
                uploads = s.scalar(select(Asset).join(Task, Task.source_asset_id == Asset.id).where(Asset.user_id == user.id, Asset.conversation_id == conversation.id, Asset.path != "", Asset.expired.is_(False), Task.kind == "upload", Task.status == "succeeded").order_by(Asset.id.desc()).limit(1))
                target = s.scalar(select(Asset).join(Task, Task.result_asset_id == Asset.id).where(Asset.user_id == user.id, Asset.conversation_id == conversation.id, Asset.path != "", Asset.expired.is_(False), Task.kind.in_(["image", "edit"]), Task.status == "succeeded").order_by(Asset.id.desc()).limit(1))
                if uploads and target:
                    result = self.enqueue(s, user, conversation, "edit", argument, channel, target.id)
                    s.get(Task, result["task_id"]).usage = {"reference_asset_ids": [uploads.id]}
                    return result
            match = re.fullmatch(r"#?(\d+)\s+(.+)", argument, re.S)
            if match:
                asset = s.scalar(select(Asset).where(Asset.id == number(match[1]), Asset.user_id == user.id, Asset.conversation_id == conversation.id))
                prompt = match[2]
            else:
                if not argument or argument.startswith("#"):
                    raise UserError("请补充修改要求，例如：修改这张图，把背景改成夜晚。")
                pending = s.scalar(select(Task.id).where(Task.user_id == user.id, Task.conversation_id == conversation.id, Task.kind.in_(["image", "edit", "upload"]), Task.status.in_(["queued", "running", "uncertain"])).limit(1))
                if pending:
                    raise UserError("还有图片正在处理或结果待核查，请等图片收到后再修改；修改更早的图片可用 /图片 选择。")
                asset = s.scalar(select(Asset).where(Asset.user_id == user.id, Asset.conversation_id == conversation.id).order_by(Asset.id.desc()).limit(1))
                if not asset:
                    raise UserError("这个对话还没有图片。请先发一张图片，或告诉我想画什么。")
                prompt = argument
            if not asset or asset.expired:
                raise UserError("图片不存在、不属于当前会话或已过期。请重新上传，或切换到图片所属会话。")
            return self.enqueue(s, user, conversation, "edit", prompt, channel, asset.id)
        if text.startswith("/"):
            raise UserError("暂不支持这个指令，输入 /帮助 查看用法。")
        if is_image_request(text):
            return self.enqueue(s, user, conversation, "image", text, channel)
        return self.enqueue(s, user, conversation, "text", text, channel)

    @staticmethod
    def usage(s, user):
        rows = s.execute(select(Task.kind, func.count()).where(Task.user_id == user.id, Task.day == today()).group_by(Task.kind)).all()
        counts = dict(rows)
        credits, fen = s.execute(select(func.coalesce(func.sum(Task.credits), 0), func.coalesce(func.sum(Task.reserve_fen), 0)).where(Task.day == today())).one()
        return counts.get("text", 0), counts.get("image", 0)+counts.get("edit", 0), credits, fen

    def enqueue(self, s, user, conversation, kind, prompt, channel, source=None):
        if not prompt:
            raise UserError("请补充具体要求。")
        texts, images, total, reserved = self.usage(s, user)
        image = kind != "text"
        unlimited = self.unlimited(user)
        if not unlimited and (images >= user.image_limit if image else texts >= user.text_limit):
            raise UserError("今日个人额度已用完，请联系管理员。")
        credits = 20 if image else 1
        fen = self.settings.image_reserve_fen if image else self.settings.text_reserve_fen
        if not unlimited and total + credits > self.settings.daily_credit_limit:
            raise UserError("今日全体资源额度已达上限。")
        if not unlimited and self.settings.daily_budget_fen and reserved + fen > self.settings.daily_budget_fen:
            raise UserError("今日费用预留预算已达上限。")
        task = Task(user_id=user.id, conversation_id=conversation.id, kind=kind, prompt=prompt, source_asset_id=source, channel=channel, day=today(), credits=credits, reserve_fen=fen)
        s.add(task)
        s.flush()
        s.add(Message(conversation_id=conversation.id, task_id=task.id, role="user", content=prompt))
        if conversation.title == "新对话":
            conversation.title = prompt.replace("\n", " ")[:32]
        conversation.updated_at = utcnow()
        feedback = {"text": "收到，正在思考。", "image": "正在为你画图，完成后会发在这里。", "edit": "正在按你的要求修改图片，完成后发给你。"}
        return {"text": feedback[kind], "task_id": task.id, "conversation_id": conversation.id, "silent": kind == "text"}

    def unlimited(self, user):
        return user.corp_id == self.settings.dingtalk_corp_id and user.staff_id in {x.strip() for x in self.settings.unlimited_staff_ids.split(",") if x.strip()}
