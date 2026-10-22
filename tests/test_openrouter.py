import base64
import json
import uuid

import httpx
import pytest
from dotenv import dotenv_values

from app.config import Settings
from app.db import Asset, Database, Task
from app.providers import MockProvider, OpenRouterProvider, ProviderError, make_provider
from app.service import Service
from app.worker import Worker
from scripts.configure_openrouter import configure


@pytest.fixture
def router():
    return Settings(provider="openrouter", openrouter_api_key="router-fixture-secret")


def mock_http(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize("summary,limit", [(False,1600),(True,600)])
def test_text_and_summary_use_only_openrouter_credentials(router, monkeypatch, summary, limit):
    router.text_api_key = "must-not-send-direct-key"
    router.text_base_url = "https://api.openai.com/v1"
    seen = []
    def handler(request):
        seen.append(request)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer router-fixture-secret"
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-4.1-mini"
        assert body["max_completion_tokens"] == limit
        assert body["stream"] is False
        assert body["provider"]["allow_fallbacks"] is False
        return httpx.Response(200, json={"choices":[{"message":{"content":"answer"}}],"usage":{"total_tokens":20,"cost":0.001}})
    mock_http(monkeypatch, handler)
    output = make_provider(router).text([{"role":"user","content":"test"}], summary=summary)
    assert output.text == "answer" and output.usage["cost"] == 0.001
    assert len(seen) == 1


@pytest.mark.parametrize("edit", [False,True])
def test_image_api_and_reference_binding(router, monkeypatch, edit):
    image = MockProvider().image("fixture").image
    router.image_extra = {"n":8, "stream":True, "aspect_ratio":"16:9", "input_references":[{"url":"https://wrong.example"}]}
    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/images"
        assert request.headers["Authorization"] == "Bearer router-fixture-secret"
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-image-2"
        assert body["n"] == 1 and body["stream"] is False
        assert body["aspect_ratio"] == "16:9"
        if edit:
            reference = body["input_references"][0]
            assert reference["type"] == "image_url"
            assert base64.b64decode(reference["image_url"]["url"].split(",")[1]) == image
            assert len(body["input_references"]) == 1
        else:
            assert "input_references" not in body
        return httpx.Response(200, json={"data":[{"b64_json":base64.b64encode(image).decode(),"media_type":"image/png"}],"usage":{"cost":0.04}})
    mock_http(monkeypatch, handler)
    output = OpenRouterProvider(router).image("test", image if edit else None)
    assert output.image == image and output.usage["cost"] == 0.04


def test_two_image_references_keep_target_first(router, monkeypatch):
    first = MockProvider().image("target").image
    second = MockProvider().image("logo").image
    def handler(request):
        refs = json.loads(request.content)["input_references"]
        assert [base64.b64decode(r["image_url"]["url"].split(",")[1]) for r in refs] == [first, second]
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(first).decode()}]})
    mock_http(monkeypatch, handler)
    assert OpenRouterProvider(router).image("replace logo", [first, second]).image == first


def test_helpers_use_cheap_model_without_answer_reasoning(router, monkeypatch):
    router.openrouter_text_model = "openai/gpt-6-astra"
    router.text_extra = {"reasoning": {"effort": "medium"}}
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["model"] == "openai/gpt-4.1-mini"
        assert "reasoning" not in body
        value = '{"action":"text","target_id":null}' if "response_format" in body else "摘要"
        return httpx.Response(200, json={"choices": [{"message": {"content": value}}], "usage": {"cost": 0.0001}})
    mock_http(monkeypatch, handler)
    provider = OpenRouterProvider(router)
    assert provider.route("你好", [])[0]["action"] == "text"
    assert seen[0]["max_completion_tokens"] == 256
    assert provider.text([], summary=True).text == "摘要"



def test_stream_text_preserves_delta_order_and_usage(router, monkeypatch):
    def handler(request):
        assert json.loads(request.content)["stream"] is True
        events = [{"choices": [{"delta": {"content": "第一段"}}]}, {"choices": [{"delta": {"content": "第二段"}}]}, {"choices": [], "usage": {"cost": 0.001}}]
        wire = "".join("data: " + json.dumps(x) + "\n\n" for x in events) + "data: [DONE]\n\n"
        return httpx.Response(200, text=wire)
    mock_http(monkeypatch, handler)
    deltas = []
    output = OpenRouterProvider(router).text_stream([], deltas.append)
    assert deltas == ["第一段", "第二段"]
    assert output.text == "第一段第二段" and output.usage["cost"] == 0.001


def test_stream_disconnect_is_uncertain_without_retry(router, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
    mock_http(monkeypatch, handler)
    deltas = []
    with pytest.raises(ProviderError) as failure:
        OpenRouterProvider(router).text_stream([], deltas.append)
    assert failure.value.uncertain and deltas == ["partial"] and len(calls) == 1


def test_openrouter_config_does_not_require_direct_openai_key(router):
    router.validate()
    assert isinstance(make_provider(router), OpenRouterProvider)


def test_missing_openrouter_key_does_not_fall_back_to_openai_key(router):
    router.openrouter_api_key = ""
    router.text_api_key = router.image_api_key = "old-direct-key"
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        router.validate()
    router.validate(require_credentials=False)


@pytest.mark.parametrize("model", ["gpt-4.1-mini", "", "https://example.com/model"])
def test_openrouter_requires_model_slug(router, model):
    router.openrouter_text_model = model
    with pytest.raises(ValueError, match="完整模型 ID"):
        router.validate()


@pytest.mark.parametrize("status,uncertain", [(402,False),(429,False),(500,True)])
def test_router_errors_are_redacted_and_not_retried(router, monkeypatch, status, uncertain):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="router-fixture-secret private prompt")
    mock_http(monkeypatch, handler)
    with pytest.raises(ProviderError) as error:
        OpenRouterProvider(router).image("test")
    assert len(calls) == 1
    assert error.value.uncertain is uncertain
    assert "secret" not in str(error.value) and "private" not in str(error.value)


def test_router_refuses_url_only_image_response_without_downloading(router, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data":[{"url":"https://untrusted.example/image.png"}]})
    mock_http(monkeypatch, handler)
    with pytest.raises(ProviderError):
        OpenRouterProvider(router).image("test")
    assert len(calls) == 1


def test_configure_preserves_dingtalk_and_existing_secrets(tmp_path):
    path = tmp_path / ".env"
    original = "# local settings\nPROVIDER=mock\nDINGTALK_CORP_ID=corp\nDINGTALK_CLIENT_ID=client\nDINGTALK_CLIENT_SECRET='secret # literal'\nDINGTALK_ROBOT_CODE=robot\nTEXT_API_KEY=direct-secret\nADMIN_TOKEN=admin-secret\nOPENROUTER_API_KEY=existing-router-key\nOPENROUTER_TEXT_MODEL=openai/custom-model\n"
    path.write_text(original, encoding="utf-8")
    before = dotenv_values(path)
    assert configure(path)
    after = dotenv_values(path)
    assert after["PROVIDER"] == "openrouter"
    assert after["OPENROUTER_IMAGE_MODEL"] == "openai/gpt-image-2"
    assert all(after[k] == v for k,v in before.items() if k != "PROVIDER")
    assert "DINGTALK_CLIENT_SECRET='secret # literal'" in path.read_text(encoding="utf-8")
    assert configure(path)
    assert dotenv_values(path) == after


def test_worker_stores_openrouter_outputs_and_image_lineage(router, monkeypatch, tmp_path):
    router.data_dir = tmp_path
    router.database_url = f"sqlite:///{tmp_path.as_posix()}/router.db"
    db = Database(router)
    db.init()
    image = MockProvider().image("fixture").image
    def handler(request):
        if request.url.path.endswith("chat/completions"):
            if json.loads(request.content).get("response_format"):
                return httpx.Response(200, json={"choices": [{"message": {"content": '{"action":"text"}'}}], "usage": {"cost": 0.0001}})
            return httpx.Response(200, json={"choices":[{"message":{"content":"真实接口格式模拟回复"}}],"usage":{"cost":0.002}})
        return httpx.Response(200, json={"data":[{"b64_json":base64.b64encode(image).decode()}],"usage":{"cost":0.04}})
    mock_http(monkeypatch, handler)
    service, worker = Service(db, router), Worker(db, router)
    service.provision("test-corp", "staff")
    def run(text):
        result = service.receive("test-corp", "staff", uuid.uuid4().hex, text)
        worker.run_once()
        with db.Session() as s:
            task = s.get(Task, result["task_id"])
            assert task.status == "succeeded"
            return task
    try:
        assert run("hello").usage["answer"]["cost"] == 0.002
        parent = run("/生图 生成图片").result_asset_id
        edited = run(f"/改图 #{parent} 修改图片")
        with db.Session() as s:
            assert s.get(Asset, edited.result_asset_id).parent_id == parent
            assert edited.usage["cost"] == 0.04
    finally:
        db.engine.dispose()
