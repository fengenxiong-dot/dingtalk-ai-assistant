"""Operator check: report which host DingTalk returns for an image download code.

Prints scheme/host/port only; tokens and URL query values are never printed.
"""
import argparse
import json
from urllib.parse import urlparse

import httpx

from app.config import Settings


def main():
    parser = argparse.ArgumentParser(description="检查钉钉图片下载地址是否落在白名单域名内")
    parser.add_argument("code", help="钉钉回调中的 downloadCode")
    args = parser.parse_args()
    settings = Settings.load()
    report = {"allowed_hosts": settings.dingtalk_download_hosts}
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        token_response = client.post("https://api.dingtalk.com/v1.0/oauth2/accessToken", json={"appKey": settings.dingtalk_client_id, "appSecret": settings.dingtalk_client_secret})
        token = token_response.json().get("accessToken", "") if token_response.is_success else ""
        if not token:
            report["access_token"] = {"ok": False, "http": token_response.status_code}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        response = client.post(
            "https://api.dingtalk.com/v1.0/robot/messageFiles/download",
            headers={"x-acs-dingtalk-access-token": token},
            json={"robotCode": settings.dingtalk_robot_code, "downloadCode": args.code},
        )
    try:
        data = response.json()
    except ValueError:
        data = {}
    url = data.get("downloadUrl", "")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    allowed = [x.strip() for x in settings.dingtalk_download_hosts.split(",") if x.strip()]
    report["download_api"] = {
        "http": response.status_code,
        "errcode": data.get("errcode"),
        "errmsg": data.get("errmsg", ""),
        "has_url": bool(url),
        "scheme": parsed.scheme,
        "host": host,
        "port": parsed.port,
        "path": parsed.path,
        "has_query": bool(parsed.query),
        "matches_allowlist": any(host == suffix or host.endswith("." + suffix) for suffix in allowed),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
