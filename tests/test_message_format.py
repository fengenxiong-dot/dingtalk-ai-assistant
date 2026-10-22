import json
import httpx
from app.config import Settings
from app.dingtalk import DingTalkTransport
from app.message_format import chunks
from app.message_format import normalize, has_markdown


def test_escaped_chinese_bold_is_repaired_and_separated():
    value = normalize(r"\*\*吸引观看：\*\*地方传说的AI演绎。")
    assert value == " **吸引观看：** 地方传说的AI演绎。"
    assert has_markdown(value)
    assert normalize("**判断：这个方向有潜力。**先证明故事准确。") == " **判断：这个方向有潜力。** 先证明故事准确。"


def test_code_keeps_escaped_stars_verbatim():
    value = r"`\*\*literal\*\*`" + "\n```python\n" + r"x = '\*\*literal\*\*'" + "\n```"
    assert normalize(value) == value


def test_markdown_and_plain_messages_use_correct_dingtalk_type(monkeypatch):
    seen = []
    def request(self, url, **kwargs):
        body = kwargs["json"]
        seen.append((body["msgKey"], json.loads(body["msgParam"])))
        return {"processQueryKey": "accepted"}
    monkeypatch.setattr(DingTalkTransport, "access_token", lambda self: "fixture")
    monkeypatch.setattr(DingTalkTransport, "request", request)
    transport = DingTalkTransport(Settings())
    transport.send("alice", "## 方案\n\n**重点**\n- 第一项")
    transport.send("alice", "正在生成图片。")
    assert seen[0][0] == "sampleMarkdown"
    assert seen[0][1]["text"].startswith("## 方案")
    assert seen[1] == ("sampleText", {"content": "正在生成图片。"})


def test_long_code_messages_have_balanced_fences_and_byte_limit():
    result = chunks("## 示例\n```python\n" + "print('你好')\n" * 700 + "```\n结束")
    assert len(result) > 1
    assert all(len(value.encode()) <= 2800 for value in result)
    assert all(value.count("```") % 2 == 0 for value in result)
    assert sum(value.count("print('你好')") for value in result) == 700
