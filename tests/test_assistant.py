import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import admin
from app.api import create_app
from app.config import Settings
from app.db import Asset, Conversation, Database, Message, Outbox, Task, User, utcnow
from app.dingtalk import parse_callback
from app.providers import MockProvider, Output, ProviderError
from app.service import Service, UserError, today
from app.worker import Worker


@pytest.fixture
def env(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path.as_posix()}/test.db", data_dir=tmp_path, admin_token="test-admin-secret", dev_token="test-dev-secret", context_byte_budget=4096)
    db = Database(settings)
    db.init()
    service = Service(db, settings)
    service.provision("local", "alice", "Alice")
    service.provision("local", "bob", "Bob")
    yield settings, db, service
    db.engine.dispose()


def send(service, text, user="alice", message_id=None, **kwargs):
    return service.receive("local", user, message_id or uuid.uuid4().hex, text, **kwargs)


def task_of(db, result):
    with db.Session() as s:
        return s.get(Task, result["task_id"])


@pytest.mark.parametrize("prompt,kind", [
    ("生成一只猫咪穿着土家族服饰围在篝火旁", "image"),
    ("请帮我画一只猫", "image"),
    ("生成一张图片：猫咪围在篝火旁", "image"),
    ("生成一个故事，猫咪围在篝火旁", "text"),
    ("生成一张表格", "text"),
    ("画一只猫的提示词怎么写", "text"),
    ("不要生成图片", "text"),
    ("你能生成图片吗", "text"),
])
def test_natural_image_routing(env, prompt, kind):
    _, db, service = env
    task = task_of(db, send(service, prompt))
    assert task.kind == kind
    assert task.prompt == prompt
    assert task.credits == (20 if kind == "image" else 1)


def test_conversation_history_archive_resume_and_ownership(env):
    settings, db, service = env
    first = send(service, "培训预算为 8500 元")
    Worker(db, settings).run_once()
    old_id = first["conversation_id"]
    send(service, "/新对话 招聘")
    assert "8500" in send(service, "/搜索 8500")["text"]
    send(service, f"/归档 {old_id}")
    assert "（归档）" in send(service, "/搜索 8500")["text"]
    assert "8500" in send(service, f"/记录 {old_id}")["text"]
    assert "8500" not in send(service, "/搜索 8500", "bob")["text"]
    assert "权限" in send(service, f"/继续 {old_id}", "bob")["text"]
    assert "权限" in send(service, f"/记录 {old_id}", "bob")["text"]
    assert send(service, f"/继续 {old_id}")["conversation_id"] == old_id
    assert send(service, "继续讨论")["conversation_id"] == old_id


def test_duplicate_receipt_is_atomic_even_under_concurrency(env):
    settings, db, service = env
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: send(service, "一次调用", message_id="same-id"), range(16)))
    assert len({r["task_id"] for r in results}) == 1
    with db.Session() as s:
        assert s.scalar(select(func.count()).select_from(Task)) == 1
        assert s.scalar(select(func.count()).select_from(Message)) == 1


def test_personal_quota_is_reserved_before_execution(env):
    _, db, service = env
    service.provision("local", "alice", text_limit=2)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda i: send(service, f"问题 {i}"), range(8)))
    assert sum("task_id" in r for r in results) == 2
    assert sum("额度" in r["text"] for r in results) == 6


def test_global_budget_across_users(env):
    settings, _, service = env
    settings.daily_credit_limit = 2
    assert "task_id" in send(service, "A", "alice")
    assert "task_id" in send(service, "B", "bob")
    assert "全体资源" in send(service, "C")["text"]


def test_fee_reservation_is_atomic(env):
    settings, _, service = env
    settings.text_reserve_fen, settings.image_reserve_fen, settings.daily_budget_fen = 10, 100, 15
    assert "task_id" in send(service, "A")
    assert "费用预留预算" in send(service, "B", "bob")["text"]


def test_unknown_and_disabled_users_cannot_submit(env):
    _, _, service = env
    with pytest.raises(UserError):
        send(service, "hello", "stranger")
    service.provision("local", "alice", enabled=False)
    with pytest.raises(UserError):
        send(service, "hello")


class CapturingProvider(MockProvider):
    def __init__(self):
        self.calls = []

    def text(self, messages, summary=False):
        self.calls.append((messages, summary))
        return Output(text="历史重点 8500 元" if summary else "已记录")


def test_context_never_reads_future_messages_or_other_conversations(env):
    settings, db, service = env
    provider = CapturingProvider()
    worker = Worker(db, settings, provider)
    send(service, "FIRST")
    send(service, "FUTURE")
    send(service, "/新对话 other")
    send(service, "OTHER")
    send(service, "BOB SECRET", "bob")
    worker.run_once()
    context = str(provider.calls[0])
    assert "FIRST" in context
    assert all(x not in context for x in ("FUTURE", "OTHER", "BOB SECRET"))
    worker.run_once()
    assert "FIRST" in str(provider.calls[1]) and "FUTURE" in str(provider.calls[1])


def test_long_context_compacts_but_keeps_searchable_originals(env):
    settings, db, service = env
    provider = CapturingProvider()
    worker = Worker(db, settings, provider)
    conversation = None
    for i in range(14):
        result = send(service, f"预算8500 第{i}条 " + "约束条件"*80)
        conversation = result["conversation_id"]
        worker.run_once()
    assert any(summary for _, summary in provider.calls)
    for messages, summary in provider.calls:
        if not summary:
            assert sum(len(m["content"].encode())+32 for m in messages) <= settings.context_byte_budget
    with db.Session() as s:
        c = s.get(Conversation, conversation)
        assert c.summary_through > 0
        assert s.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation)) == 28
    assert "第0条" in send(service, "/搜索 第0条")["text"]


def test_plain_text_delivers_only_answer_without_internal_ids(env):
    settings, db, service = env
    result = send(service, "你好", channel="dingtalk")
    with db.Session() as s:
        assert s.scalar(select(func.count()).select_from(Outbox)) == 0
    Worker(db, settings).run_once()
    with db.Session() as s:
        rows = s.scalars(select(Outbox)).all()
        assert len(rows) == 1
        assert rows[0].task_id == result["task_id"]
        assert rows[0].text == s.get(Task, result["task_id"]).result
        assert "[会话 #" not in rows[0].text


def test_simple_image_feedback_and_latest_image_edit(env):
    settings, db, service = env
    worker = Worker(db, settings)
    original = send(service, "/生图 猫", channel="dingtalk")
    assert "#" not in original["text"]
    assert "正在" in send(service, "修改这张图，把背景改成夜晚")["text"]
    worker.run_once()
    parent = task_of(db, original).result_asset_id
    with db.Session() as s:
        rows = s.scalars(select(Outbox).order_by(Outbox.id)).all()
        assert len(rows) == 3
        assert rows[-1].asset_id == parent
        assert all("#" not in row.text for row in rows)
    edit = send(service, "修改这张图，把背景改成夜晚")
    assert task_of(db, edit).source_asset_id == parent
    worker.run_once()
    edited = task_of(db, edit).result_asset_id
    next_edit = send(service, "/改图 增加星星")
    assert task_of(db, next_edit).source_asset_id == edited
    assert "还没有图片" in send(service, "修改这张图，把背景改成夜晚", "bob")["text"]
    send(service, "/新对话 other")
    assert "还没有图片" in send(service, "修改这张图，把背景改成夜晚")["text"]


def test_image_parentage_and_explicit_source(env):
    settings, db, service = env
    worker = Worker(db, settings)
    a = send(service, "/生图 原图")
    worker.run_once()
    source = task_of(db, a).result_asset_id
    b = send(service, f"/改图 #{source} 改蓝色")
    worker.run_once()
    result = task_of(db, b)
    with db.Session() as s:
        assert s.get(Asset, result.result_asset_id).parent_id == source
    assert "图片不存在" in send(service, f"/改图 #{source} 改红色", "bob")["text"]
    send(service, "/新对话 other")
    assert "图片不存在" in send(service, f"/改图 #{source} 改红色")["text"]


def test_poster_routing_uses_request_not_entire_body(env):
    _, db, service = env
    result = send(service, "根据内容，生成一张适合手机阅读的校招宣传长图\n公司提供整体解决方案")
    assert task_of(db, result).kind == "image"


def test_cost_context_cap_preserves_current_question_and_original_history(env):
    settings, db, service = env
    settings.context_byte_budget = 8000
    worker = Worker(db, settings)
    first = send(service, "原始预算为8500元。" * 80)
    worker.run_once()
    for i in range(6):
        send(service, f"第{i}轮：" + "讨论实施步骤。" * 90)
        worker.run_once()
    question = "请根据讨论制定计划。" * 80
    result = send(service, question)
    messages, _ = worker.context(task_of(db, result))
    assert messages[-1]["content"] == question
    assert sum(len(m["content"].encode()) + 32 for m in messages) <= 8000
    with db.Session() as s:
        assert "8500" in s.scalar(select(Message.content).where(Message.task_id == first["task_id"], Message.role == "user"))


def test_cost_breakdown_no_double_count_and_owner_only(env):
    _, db, service = env
    result = send(service, "/生图 test")
    with db.write() as s:
        s.get(Task, result["task_id"]).usage = {"routing": {"cost": 0.052555}, "cost": 0.02813, "cost_details": {"upstream_inference_cost": 0.02813}}
    reply = send(service, "/费用")["text"]
    assert "$0.080685" in reply and "意图识别" in reply and "图片生成/修改" in reply
    assert "没有找到" in send(service, f'/费用 {result["task_id"]}', "bob")["text"]


def test_semantic_routing_executes_image_and_contextual_edit(env):
    settings, db, service = env
    class RoutedProvider(MockProvider):
        def route(self, prompt, images):
            if "公司大楼" in prompt:
                return {"action": "image"}, {"cost": 0.001}
            return {"action": "edit", "target_id": images[0]["id"]}, {"cost": 0.001}
    worker = Worker(db, settings, provider=RoutedProvider())
    first = send(service, "生成一个公司大楼")
    worker.run_once()
    task = task_of(db, first)
    assert task.kind == "image" and task.status == "succeeded"
    second = send(service, "把“企业”改成“企业精密”")
    worker.run_once()
    edited = task_of(db, second)
    assert edited.kind == "edit" and edited.source_asset_id == task.result_asset_id
    assert "routing" in edited.usage
    messages, _ = worker.context(task_of(db, send(service, "图片做了什么")))
    assert any("图片操作历史" in m["content"] and "企业精密" in m["content"] for m in messages)


def test_semantic_route_rejects_other_users_image(env):
    settings, db, service = env
    worker = Worker(db, settings)
    original = send(service, "/生图 bob图", "bob")
    worker.run_once()
    foreign = task_of(db, original).result_asset_id
    class BadRoute(MockProvider):
        def route(self, prompt, images):
            return {"action": "edit", "target_id": foreign}, {}
    result = send(service, "把名字改掉")
    Worker(db, settings, provider=BadRoute()).run_once()
    assert task_of(db, result).status == "failed"
    assert task_of(db, result).result_asset_id is None


def test_edit_without_punctuation(env):
    settings, db, service = env
    first = send(service, "/生图 logo")
    Worker(db, settings).run_once()
    second = send(service, "修改这张图把颜色换成蓝色")
    assert task_of(db, second).source_asset_id == task_of(db, first).result_asset_id


def test_streamed_answer_is_available_before_generation_finishes(env):
    settings, db, service = env
    first = "**先给结论**\n\n" + "第一段内容。" * 180 + "\n\n"
    second = "第二段解释。"
    class StreamingProvider(MockProvider):
        def text_stream(self, messages, on_delta):
            on_delta(first)
            with db.Session() as s:
                rows = s.scalars(select(Outbox).order_by(Outbox.id)).all()
                assert rows and rows[0].text.startswith("**先给结论**")
                assert s.get(Task, result["task_id"]).status == "running"
            on_delta(second)
            return Output(text=first + second, usage={"total_tokens": 20})
    result = send(service, "分析一下", channel="dingtalk")
    Worker(db, settings, provider=StreamingProvider()).run_once()
    task = task_of(db, result)
    assert task.status == "succeeded" and task.result == first + second
    with db.Session() as s:
        rows = s.scalars(select(Outbox).order_by(Outbox.id)).all()
        assert rows[-1].text == second
        assert sum(row.text.count("第一段内容。") for row in rows) >= 178
        assert all(len(row.text.encode()) <= 2800 for row in rows)
        assert s.scalar(select(func.count()).select_from(Message).where(Message.task_id == task.id, Message.role == "assistant")) == 1


def test_unlimited_test_account_is_scoped_and_bypasses_global_caps(env):
    settings, db, service = env
    settings.dingtalk_corp_id = "local"
    settings.unlimited_staff_ids = "alice"
    settings.daily_credit_limit = 0
    settings.daily_budget_fen = 1
    settings.image_reserve_fen = 100
    service.provision("local", "alice", text_limit=0, image_limit=0)
    assert task_of(db, send(service, "/生图 test")).kind == "image"
    assert task_of(db, send(service, "hello")).kind == "text"
    assert "不限次数" in send(service, "/额度")["text"]
    assert "全体资源" in send(service, "hello", "bob")["text"]


def test_upload_failure_stops_dependent_edit_without_double_error(env):
    settings, db, service = env
    worker = Worker(db, settings)
    original = send(service, "/生图 海报")
    worker.run_once()
    class BrokenTransport:
        def download(self, code):
            raise ProviderError("download failed")
    worker.transport = BrokenTransport()
    result = send(service, "替换左上角的logo", download_code="test", channel="dingtalk")
    assert task_of(db, result).source_asset_id == task_of(db, original).result_asset_id
    worker.run_once()
    worker.run_once()
    assert task_of(db, result).status == "failed"
    with db.Session() as s:
        failures = s.scalars(select(Outbox).where(Outbox.text.contains("上传成功"))).all()
        assert len(failures) == 1
        assert s.get(Asset, task_of(db, original).result_asset_id).path


def test_template_poster_and_logo_edit_preserve_text(env):
    settings, db, service = env
    worker = Worker(db, settings)
    prompt = "根据内容，生成一张校招宣传长图\n企业2027校园招聘\n工作地点：武汉\n简历投递：https://example.com/jobs"
    original = send(service, prompt)
    worker.run_once()
    parent = task_of(db, original).result_asset_id
    class Transport:
        def download(self, code):
            return MockProvider().image("logo").image
    worker.transport = Transport()
    result = send(service, "替换海报上的Logo", download_code="logo")
    worker.run_once()
    worker.run_once()
    edited = task_of(db, result)
    assert edited.status == "succeeded"
    assert edited.source_asset_id == parent
    assert edited.usage["template"] is True
    with db.Session() as s:
        asset = s.get(Asset, edited.result_asset_id)
        import json
        assert json.loads(worker.storage.resolve(asset.path).with_suffix(".json").read_text(encoding="utf-8"))["prompt"] == prompt


def test_upload_and_edit_share_exact_asset_and_download_first(env):
    settings, db, service = env
    class Transport:
        def download(self, code):
            assert code == "test-code"
            return MockProvider().image("fixture").image
    worker = Worker(db, settings, transport=Transport())
    result = send(service, "/改图 修改", download_code="test-code")
    edit = task_of(db, result)
    assert edit.kind == "edit"
    worker.run_once()
    worker.run_once()
    with db.Session() as s:
        source = s.get(Asset, edit.source_asset_id)
        assert source.path and not source.download_code
        edited = s.get(Asset, task_of(db, result).result_asset_id)
        assert edited.parent_id == source.id


def test_invalid_image_message_does_not_leave_partial_state(env):
    _, db, service = env
    result = send(service, "/改图", download_code="some-code")
    assert "填写修改要求" in result["text"]
    with db.Session() as s:
        assert s.scalar(select(func.count()).select_from(Asset)) == 0
        assert s.scalar(select(func.count()).select_from(Task)) == 0


def test_timeout_never_automatically_regenerates_or_refunds(env):
    settings, db, service = env
    class Broken(MockProvider):
        def text(self, *args, **kwargs):
            raise ProviderError("timeout", True)
    result = send(service, "test")
    worker = Worker(db, settings, Broken())
    assert worker.run_once()
    assert not worker.run_once()
    assert task_of(db, result).status == "uncertain"
    assert task_of(db, result).credits == 1


def test_restart_marks_running_unknown_but_preserves_queued_work(env):
    settings, db, service = env
    first = send(service, "started")
    second = send(service, "not started")
    with db.write() as s:
        s.get(Task, first["task_id"]).status = "running"
    worker = Worker(db, settings)
    worker.recover()
    assert task_of(db, first).status == "uncertain"
    assert task_of(db, second).status == "queued"
    worker.run_once()
    assert task_of(db, second).status == "succeeded"


def test_redelivery_never_calls_provider_again(env):
    settings, db, service = env
    class Transport:
        def send(self, *args):
            raise ProviderError("network", True)
    result = send(service, "text", channel="dingtalk")
    provider = CapturingProvider()
    worker = Worker(db, settings, provider, Transport())
    worker.run_once()
    worker.deliver_once()
    worker.deliver_once()
    with db.Session() as s:
        failed = s.scalar(select(Outbox).where(Outbox.task_id == result["task_id"]))
    admin.resend(db, failed.id)
    worker.deliver_once()
    assert len(provider.calls) == 1
    assert task_of(db, result).status == "succeeded"


def test_retention_expires_files_but_keeps_version_metadata(env):
    settings, db, service = env
    worker = Worker(db, settings)
    result = send(service, "/生图 fixture")
    worker.run_once()
    image_id = task_of(db, result).result_asset_id
    with db.write() as s:
        asset = s.get(Asset, image_id)
        asset.created_at = utcnow()-timedelta(days=31)
        path = worker.storage.resolve(asset.path)
    assert worker.cleanup() == 1
    assert not path.exists()
    assert "已过期" in send(service, "/图片")["text"]
    assert "已过期" in send(service, f"/改图 #{image_id} edit")["text"]


def test_admin_api_auth_and_data_minimization(env):
    settings, db, service = env
    send(service, "CONFIDENTIAL PERSONAL MESSAGE")
    client = TestClient(create_app(settings, db))
    assert client.get("/health").status_code == 200
    for path in ("/admin/users", "/admin/tasks", "/admin/overview", "/admin/costs"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer test-dev-secret"}).status_code == 401
        response = client.get(path, headers={"Authorization": "Bearer test-admin-secret"})
        assert response.status_code == 200
        assert "CONFIDENTIAL" not in response.text
    assert client.post("/dev/messages", json={"message_id":"1", "text":"hello"}).status_code == 401


def test_mock_http_task_access_requires_owner(env):
    settings, db, service = env
    result = send(service, "secret")
    client = TestClient(create_app(settings, db))
    headers = {"Authorization": "Bearer test-dev-secret"}
    url = f"/dev/tasks/{result['task_id']}"
    assert client.get(url+"?staff_id=bob", headers=headers).status_code == 404
    assert client.get(url+"?staff_id=alice", headers=headers).status_code == 200


def test_live_mode_rejects_dev_identity_endpoint(env):
    settings, db, _ = env
    settings.provider = "compatible"
    settings.text_base_url = settings.image_base_url = "https://api.openai.com/v1"
    settings.text_api_key = settings.image_api_key = "fixture"
    settings.text_model = settings.image_model = "test-model"
    with pytest.raises(ValueError, match="DEV_TOKEN"):
        create_app(settings, db)
    settings.dev_token = ""
    client = TestClient(create_app(settings, db))
    assert client.post("/dev/messages", json={"message_id":"1","text":"test"}).status_code == 404


def _seed_costs(db, day, staff, name, kind, usage, status="succeeded"):
    """Insert a finished task with a given usage shape, bypassing the worker."""
    with db.write() as s:
        user = s.scalar(select(User).where(User.staff_id == staff))
        if user is None:
            user = User(corp_id="local", staff_id=staff, name=name)
            s.add(user)
            s.flush()
        conversation = s.scalar(select(Conversation).where(Conversation.user_id == user.id))
        if conversation is None:
            conversation = Conversation(user_id=user.id, title="fixture")
            s.add(conversation)
            s.flush()
        row = Task(user_id=user.id, conversation_id=conversation.id, kind=kind, prompt="fixture", status=status, usage=usage, day=day, channel="local")
        s.add(row)
        s.flush()
        return row.id


def test_admin_costs_sum_provider_reported_spend_across_shapes(env):
    settings, db, service = env
    day = today()
    # Text keeps routing/answer as separate calls; images put cost at top level.
    _seed_costs(db, day, "alice", "Alice", "text", {"routing": {"cost": 0.0003}, "answer": {"cost": 0.02}})
    _seed_costs(db, day, "alice", "Alice", "image", {"routing": {"cost": 0.0003}, "cost": 0.032})
    _seed_costs(db, day, "bob", "Bob", "edit", {"cost": 0.043})
    _seed_costs(db, day, "bob", "Bob", "upload", {})
    report = admin.costs(db, days=7)
    assert report["currency"] == "USD"
    assert report["totals"]["tasks"] == 4
    assert report["totals"]["usd"] == "0.0956"
    assert report["totals"]["by_kind"]["text"] == "0.0203"
    assert report["totals"]["by_kind"]["edit"] == "0.043"
    by_name = {u["name"]: u["usd"] for u in report["by_user"]}
    assert by_name["Bob"] == "0.043"
    assert by_name["Alice"] == "0.0526"
    assert report["by_user"][0]["name"] == "Alice"
    assert report["by_day"][0]["day"] == day


def test_admin_costs_counts_unreported_cost_and_excludes_older_days(env):
    settings, db, service = env
    day = today()
    older = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=40)).date().isoformat()
    _seed_costs(db, day, "alice", "Alice", "text", {"answer": {}})
    _seed_costs(db, older, "alice", "Alice", "text", {"answer": {"cost": 9.99}})
    report = admin.costs(db, days=7)
    assert report["totals"]["tasks"] == 1
    assert report["totals"]["usd"] == "0"
    assert report["totals"]["tasks_without_reported_cost"] == 1
    assert report["totals"]["tasks_with_reported_cost"] == 0
    assert admin.costs(db, days=60)["totals"]["usd"] == "9.99"


def test_admin_overview_reports_actual_spend_alongside_reservation(env):
    settings, db, service = env
    _seed_costs(db, today(), "alice", "Alice", "text", {"answer": {"cost": 0.0125}})
    overview = admin.overview(db, settings)
    assert overview["reported_cost_usd_today"] == "0.0125"
    assert overview["credits_reserved"] == 0
    assert "下限" in overview["currency_note"]


def test_parser_rejects_group_foreign_corp_and_multiple_images():
    settings = Settings(dingtalk_corp_id="our-corp", dingtalk_robot_code="our-bot")
    data = {"conversationType":"1", "chatbotCorpId":"our-corp", "robotCode":"our-bot", "senderStaffId":"alice", "msgId":"m1", "msgtype":"text", "text":{"content":"hello"}}
    assert parse_callback(data, settings)["staff_id"] == "alice"
    for changed in ({"conversationType":"2"}, {"chatbotCorpId":"foreign"}, {"senderCorpId":"foreign"}, {"senderStaffId":""}, {"robotCode":"other"}):
        with pytest.raises(UserError):
            parse_callback({**data, **changed}, settings)
    with pytest.raises(UserError, match="一张"):
        parse_callback({**data, "msgtype":"richText", "content":{"richText":[{"type":"picture", "downloadCode":"1"}, {"type":"picture", "downloadCode":"2"}]}}, settings)


def test_literal_search_does_not_expand_sql_wildcards(env):
    _, _, service = env
    send(service, "plain text")
    assert "未找到" in send(service, "/搜索 %")["text"]
    assert "未找到" in send(service, "/搜索 _")["text"]


def test_storage_disallows_path_traversal(env):
    settings, db, _ = env
    with pytest.raises(ValueError):
        Worker(db, settings).storage.read("../../.env")


def test_disable_after_queue_prevents_paid_call(env):
    settings, db, service = env
    provider = CapturingProvider()
    result = send(service, "before disabled")
    service.provision("local", "alice", enabled=False)
    Worker(db, settings, provider).run_once()
    assert not provider.calls
    assert task_of(db, result).status == "failed"


def test_category_search_and_view_image_are_owner_scoped(env):
    settings, db, service = env
    result = send(service, "/生图 分类测试海报")
    Worker(db, settings).run_once()
    asset_id = task_of(db, result).result_asset_id
    assert "分类测试海报" in send(service, "/分类 生图")["text"]
    assert "分类测试海报" not in send(service, "/分类 生图", "bob")["text"]
    viewed = send(service, f"/看图 {asset_id}", channel="dingtalk")
    assert viewed["send_asset_id"] == asset_id
    assert "不存在" in send(service, f"/看图 {asset_id}", "bob")["text"]
    with db.Session() as s:
        assert s.scalar(select(func.count()).select_from(Task)) == 1
        assert s.scalar(select(func.count()).select_from(Outbox).where(Outbox.asset_id == asset_id)) == 1


def test_rejected_content_gets_durable_explanation_without_model_task(env):
    _, db, service = env
    send(service, "", channel="dingtalk", rejection="请一次发送一张图片")
    with db.Session() as s:
        assert s.scalar(select(func.count()).select_from(Task)) == 0
        assert "一张图片" in s.scalar(select(Outbox.text))


def test_sender_recovery_does_not_change_model_task_state(env):
    settings, db, service = env
    result = send(service, "/生图 test", channel="dingtalk")
    with db.write() as s:
        s.scalar(select(Outbox)).status = "sending"
    Worker(db, settings).recover_deliveries()
    with db.Session() as s:
        assert s.scalar(select(Outbox.status)) == "uncertain"
        assert s.get(Task, result["task_id"]).status == "queued"


@pytest.mark.parametrize("command", ["/继续 "+"9"*100, "/最近 ²", "/记录 -1", "/改图 #"+"9"*100+" edit"])
def test_invalid_numeric_arguments_are_user_errors(env, command):
    _, _, service = env
    result = send(service, command)
    assert "有效编号" in result["text"]
