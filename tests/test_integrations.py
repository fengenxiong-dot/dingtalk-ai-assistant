import base64
import json

import httpx
import pytest

from app.config import Settings
from app.dingtalk import DingTalkTransport
from app.providers import CompatibleProvider, MockProvider, ProviderError


@pytest.fixture
def live():
    return Settings(provider="compatible", text_base_url="https://api.openai.com/v1", text_api_key="private-text-key", text_model="chosen-text-model", image_base_url="https://api.openai.com/v1", image_api_key="private-image-key", image_model="chosen-image-model")


def patch_http(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))


def test_openai_chat_request_schema_and_usage(live, monkeypatch):
    def handle(request):
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer private-text-key"
        body = json.loads(request.content)
        assert body["model"] == "chosen-text-model"
        assert body["max_completion_tokens"] == live.max_output_tokens
        assert body["stream"] is False and body["store"] is False
        return httpx.Response(200, json={"choices":[{"message":{"content":"answer"}}],"usage":{"prompt_tokens":15,"completion_tokens":6}})
    patch_http(monkeypatch, handle)
    output = CompatibleProvider(live).text([{"role":"user","content":"question"}])
    assert output.text == "answer" and output.usage["completion_tokens"] == 6


def test_openai_image_generation_and_json_edit(live, monkeypatch):
    fixture = MockProvider().image("test").image
    def handle(request):
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer private-image-key"
        assert body["n"] == 1
        if request.url.path.endswith("edits"):
            assert base64.b64decode(body["images"][0]["image_url"].split(",")[1]) == fixture
        else:
            assert request.url.path.endswith("generations")
        return httpx.Response(200, json={"data":[{"b64_json":base64.b64encode(fixture).decode()}]})
    patch_http(monkeypatch, handle)
    provider = CompatibleProvider(live)
    assert provider.image("generate").image == fixture
    assert provider.image("edit", fixture).image == fixture


def test_openai_multipart_edit(live, monkeypatch):
    live.image_edit_format = "multipart"
    fixture = MockProvider().image("test").image
    def handle(request):
        assert "multipart/form-data" in request.headers["Content-Type"]
        assert b'name="image"' in request.content
        assert fixture in request.content
        return httpx.Response(200, json={"data":[{"b64_json":base64.b64encode(fixture).decode()}]})
    patch_http(monkeypatch, handle)
    assert CompatibleProvider(live).image("edit", fixture).image == fixture


@pytest.mark.parametrize("code,uncertain", [(400,False),(401,False),(429,False),(500,True)])
def test_provider_errors_never_expose_keys_or_response_body(live, monkeypatch, code, uncertain):
    patch_http(monkeypatch, lambda _: httpx.Response(code, text="private-text-key sensitive prompt"))
    with pytest.raises(ProviderError) as error:
        CompatibleProvider(live).text([{"role":"user","content":"question"}])
    assert error.value.uncertain is uncertain
    assert "private" not in str(error.value) and "sensitive" not in str(error.value)


def test_provider_network_timeout_is_not_retried(live, monkeypatch):
    calls = []
    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-text-key", request=request)
    patch_http(monkeypatch, handle)
    with pytest.raises(ProviderError) as error:
        CompatibleProvider(live).text([{"role":"user","content":"question"}])
    assert len(calls) == 1 and error.value.uncertain
    assert "private" not in str(error.value)


def test_dingtalk_send_token_media_and_partial_failure(monkeypatch):
    calls = []
    settings = Settings(dingtalk_client_id="app", dingtalk_client_secret="secret", dingtalk_robot_code="bot")
    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith("accessToken"):
            assert json.loads(request.content) == {"appKey":"app","appSecret":"secret"}
            return httpx.Response(200, json={"accessToken":"token", "expireIn":7200})
        if request.url.path.endswith("upload"):
            assert request.url.params["access_token"] == "token"
            return httpx.Response(200, json={"errcode":0,"media_id":"@media"})
        body = json.loads(request.content)
        assert body["robotCode"] == "bot" and body["userIds"] == ["alice"]
        param = json.loads(body["msgParam"])
        if body["msgKey"] == "sampleImageMsg":
            assert param["photoURL"] == "@media"
            return httpx.Response(200, json={"processQueryKey":"receipt"})
        assert param["content"] == "text"
        return httpx.Response(200, json={"invalidStaffIdList":["alice"],"processQueryKey":"receipt"})
    patch_http(monkeypatch, handle)
    transport = DingTalkTransport(settings)
    transport.send("alice", "", MockProvider().image("test").image)
    with pytest.raises(ProviderError):
        transport.send("alice", "text")
    assert calls.count("/v1.0/oauth2/accessToken") == 1


def test_download_does_not_follow_untrusted_urls(monkeypatch):
    settings = Settings(dingtalk_robot_code="bot")
    transport = DingTalkTransport(settings)
    transport.token, transport.expires = "token", float("inf")
    calls = []
    def handle(request):
        calls.append(request.url)
        return httpx.Response(200, json={"downloadUrl":"https://169.254.169.254/credentials"})
    patch_http(monkeypatch, handle)
    with pytest.raises(ProviderError, match="域名"):
        transport.download("code")
    assert len(calls) == 1


def test_download_upgrades_plaintext_allowlisted_host(monkeypatch):
    settings = Settings(dingtalk_robot_code="bot")
    transport = DingTalkTransport(settings)
    transport.token, transport.expires = "token", float("inf")
    calls = []
    def handle(request):
        calls.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(200, json={"downloadUrl":"http://wukong-file-im-zjk.oss-cn-zhangjiakou.aliyuncs.com/ddmedia%2Fpic.png?x=1"})
        return httpx.Response(200, content=b"image-bytes")
    patch_http(monkeypatch, handle)
    assert transport.download("code") == b"image-bytes"
    assert calls[-1] == "https://wukong-file-im-zjk.oss-cn-zhangjiakou.aliyuncs.com/ddmedia%2Fpic.png?x=1"


def test_download_rejects_plaintext_unlisted_host(monkeypatch):
    settings = Settings(dingtalk_robot_code="bot")
    transport = DingTalkTransport(settings)
    transport.token, transport.expires = "token", float("inf")
    calls = []
    def handle(request):
        calls.append(request.url)
        return httpx.Response(200, json={"downloadUrl":"http://files.example.com/pic.png"})
    patch_http(monkeypatch, handle)
    with pytest.raises(ProviderError, match="域名"):
        transport.download("code")
    assert len(calls) == 1


def test_download_error_names_host_without_url(monkeypatch):
    # DingTalk rotates OSS regions; the operator must be able to extend the
    # allowlist from the stored task error alone, without a live re-run.
    # Only the host is kept: the URL query can carry credentials.
    settings = Settings(dingtalk_robot_code="bot")
    transport = DingTalkTransport(settings)
    transport.token, transport.expires = "token", float("inf")
    def handle(request):
        return httpx.Response(200, json={"downloadUrl": "https://im-media.wukongcdn.com/pic.png?OSSAccessKeyId=secret"})
    patch_http(monkeypatch, handle)
    with pytest.raises(ProviderError) as raised:
        transport.download("code")
    message = str(raised.value)
    assert "im-media.wukongcdn.com" in message
    assert "OSSAccessKeyId" not in message
    assert "/pic.png" not in message


def test_settings_require_pricing_when_budget_enabled(live):
    live.daily_budget_fen = 1000
    with pytest.raises(ValueError, match="预留费用"):
        live.validate()
