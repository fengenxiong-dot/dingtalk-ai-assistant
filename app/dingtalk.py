import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .providers import ProviderError
from .service import UserError
from .storage import MAX_IMAGE_BYTES


class SafeSDKLogFilter(logging.Filter):
    """The upstream SDK logs connection tickets at INFO and raw callbacks on errors."""
    def filter(self, record):
        if record.levelno < logging.WARNING:
            return False
        record.msg = "钉钉 SDK 连接或消息处理异常；请查看 stream-status.json 的连接状态。"
        record.args, record.exc_info, record.exc_text, record.stack_info = (), None, None, None
        return True


def connection_state(client):
    websocket = getattr(client, "websocket", None)
    return "connected" if getattr(getattr(websocket, "state", None), "name", "") == "OPEN" else "connecting"


class DingTalkTransport:
    def __init__(self, settings):
        self.s = settings
        self.token, self.expires = "", 0

    def request(self, url, **kwargs):
        try:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                response = client.post(url, **kwargs)
            if response.status_code == 401:
                self.token, self.expires = "", 0
            if not response.is_success:
                raise ProviderError(f"钉钉接口返回 HTTP {response.status_code}，请检查权限及限流。", response.status_code >= 500)
            data = response.json()
            if data.get("errcode", 0) != 0:
                raise ProviderError("钉钉接口拒绝操作，请核对应用权限和可见范围。")
            return data
        except httpx.TransportError:
            raise ProviderError("钉钉网络中断，操作结果未知。", True) from None
        except (ValueError, TypeError):
            raise ProviderError("钉钉返回格式异常，操作结果未知。", True) from None

    def access_token(self):
        if not self.token or time.monotonic() >= self.expires:
            data = self.request("https://api.dingtalk.com/v1.0/oauth2/accessToken", json={"appKey": self.s.dingtalk_client_id, "appSecret": self.s.dingtalk_client_secret})
            self.token = data["accessToken"]
            self.expires = time.monotonic() + max(1, int(data["expireIn"])-300)
        return self.token

    def download(self, code):
        data = self.request("https://api.dingtalk.com/v1.0/robot/messageFiles/download", headers={"x-acs-dingtalk-access-token": self.access_token()}, json={"robotCode": self.s.dingtalk_robot_code, "downloadCode": code})
        url = data.get("downloadUrl", "")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        allowed = [x.strip() for x in self.s.dingtalk_download_hosts.split(",") if x.strip()]
        if not any(host == suffix or host.endswith("."+suffix) for suffix in allowed):
            # Record the host only: DingTalk rotates OSS regions, so the next
            # failure must be diagnosable without re-running a live download.
            raise ProviderError(f"图片下载地址的域名不在白名单内（域名：{host or '空'}）。请在 DINGTALK_DOWNLOAD_HOSTS 中补充后重启 worker。")
        # DingTalk issues plaintext OSS URLs and moves between regions without notice;
        # upgrade allowlisted hosts to HTTPS instead of fetching over plaintext.
        if parsed.scheme == "http":
            url = parsed._replace(scheme="https").geturl()
            parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ProviderError(f"图片下载地址协议或端口不受支持（域名：{host or '空'}）。请重新发送图片。")
        try:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                with client.stream("GET", url) as response:
                    if not response.is_success:
                        raise ProviderError("图片下载失败或下载码过期，请重新上传。")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_IMAGE_BYTES:
                            raise ProviderError("图片超过 20MB，请缩小后重新发送。")
                    return bytes(content)
        except httpx.TransportError:
            raise ProviderError("图片下载网络异常，请重新上传。") from None

    def send(self, staff_id, text, image=None):
        from .message_format import has_markdown, normalize
        token = self.access_token()
        if image:
            media = self.request("https://oapi.dingtalk.com/media/upload", params={"access_token": token}, data={"type": "image"}, files={"media": ("image.png", image, "image/png")})
            if not media.get("media_id"):
                raise ProviderError("钉钉图片上传失败，未获得媒体编号。")
            key, parameter = "sampleImageMsg", {"photoURL": media["media_id"]}
        else:
            text = normalize(text)
            if has_markdown(text):
                key, parameter = "sampleMarkdown", {"title": "企业 AI 助手", "text": text}
            else:
                key, parameter = "sampleText", {"content": text}
        data = self.request("https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend", headers={"x-acs-dingtalk-access-token": token}, json={"robotCode": self.s.dingtalk_robot_code, "userIds": [staff_id], "msgKey": key, "msgParam": json.dumps(parameter, ensure_ascii=False)})
        for name in ("invalidStaffIdList", "invalidUserIdList", "flowControlledStaffIdList"):
            if data.get(name):
                raise ProviderError("钉钉未向该员工送达，请核对员工 ID、可见范围和限流。")
        if not data.get("processQueryKey"):
            raise ProviderError("未获得钉钉消息受理凭据，请核查是否已送达。", True)


def verify_identity(data, settings):
    if str(data.get("conversationType")) != "1":
        raise UserError("首版仅支持与机器人单聊。")
    if not settings.dingtalk_corp_id or data.get("chatbotCorpId") != settings.dingtalk_corp_id:
        raise UserError("企业身份不匹配。")
    if data.get("senderCorpId") and data["senderCorpId"] != settings.dingtalk_corp_id:
        raise UserError("发送者不属于本企业。")
    if data.get("robotCode") != settings.dingtalk_robot_code or not data.get("senderStaffId"):
        raise UserError("无法验证机器人或员工身份。")


def parse_callback(data, settings):
    verify_identity(data, settings)
    text, images = "", []
    kind = data.get("msgtype")
    if kind == "text":
        text = data.get("text", {}).get("content", "")
    elif kind == "picture":
        images = [data.get("content", {}).get("downloadCode", "")]
    elif kind == "richText":
        pieces = []
        for piece in data.get("content", {}).get("richText", []):
            if piece.get("type") == "picture":
                images.append(piece.get("downloadCode") or piece.get("pictureDownloadCode", ""))
            elif "text" in piece:
                pieces.append(piece["text"])
        text = "".join(pieces)
    else:
        raise UserError("首版支持文字和单张图片，请暂时不要发送其他类型文件。")
    if len(images) > 1:
        raise UserError("请每次发送一张图片，避免改错对象。")
    if images and not images[0]:
        raise UserError("未获得图片下载码，请重新发送。")
    return dict(corp_id=settings.dingtalk_corp_id, staff_id=data["senderStaffId"], message_id=data.get("msgId", ""), text=text, channel="dingtalk", download_code=images[0] if images else "")


def run_stream(service, settings):
    import dingtalk_stream
    for name in ("dingtalk_client_id", "dingtalk_client_secret", "dingtalk_corp_id", "dingtalk_robot_code"):
        if not getattr(settings, name):
            raise ValueError(f"请先在 .env 填写 {name.upper()}")
    logger = logging.getLogger("gelanruo.stream")
    activity = {"accepted_messages": 0, "rejected_messages": 0}

    class Handler(dingtalk_stream.ChatbotHandler):
        async def process(self, callback):
            try:
                verify_identity(callback.data, settings)
                # Local admin diagnostics only; never automatically grants access.
                activity["last_verified_staff_id"] = str(callback.data["senderStaffId"])[:128]
                activity["last_message_at"] = datetime.now(timezone.utc).isoformat()
                incoming = parse_callback(callback.data, settings)
                # Commit receipt + task + outbox before ACK; never invoke the model here.
                await asyncio.to_thread(service.receive, **incoming)
                activity["accepted_messages"] += 1
            except UserError as exc:
                activity["rejected_messages"] += 1
                # Explain unsupported content only to a verified, provisioned employee.
                try:
                    verify_identity(callback.data, settings)
                    await asyncio.to_thread(service.receive, corp_id=settings.dingtalk_corp_id, staff_id=callback.data["senderStaffId"], message_id=callback.data.get("msgId", ""), text="", channel="dingtalk", rejection=str(exc))
                except UserError:
                    logger.warning("收到非试用范围的消息，已拒绝；请检查应用可见范围与白名单。")
                except Exception:
                    logger.error("消息错误提示入库失败。")
                    return 500, "persist failed"
            except Exception:
                logger.error("消息入库失败，未返回成功确认。")
                return 500, "persist failed"
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    credential = dingtalk_stream.Credential(settings.dingtalk_client_id, settings.dingtalk_client_secret)
    sdk_logger = logging.getLogger("gelanruo.stream.sdk")
    sdk_logger.setLevel(logging.WARNING)
    sdk_logger.addFilter(SafeSDKLogFilter())
    client = dingtalk_stream.DingTalkStreamClient(credential, logger=sdk_logger)
    client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, Handler())
    stop = threading.Event()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    status_file = settings.data_dir / "stream-status.json"

    def monitor():
        while not stop.is_set():
            status = {"pid": os.getpid(), "state": connection_state(client), "updated_at": datetime.now(timezone.utc).isoformat(), **activity}
            try:
                temporary = status_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
                temporary.replace(status_file)
            except OSError:
                logger.warning("无法写入 Stream 本地状态文件。")
            stop.wait(2)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        client.start_forever()
    finally:
        stop.set()
        monitor_thread.join(timeout=3)
