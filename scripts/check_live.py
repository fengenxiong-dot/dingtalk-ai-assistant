"""Minimal operator smoke check. Outputs only sanitized status, never credentials."""
import argparse
import json
from dataclasses import replace
from pathlib import Path

import httpx

from app.config import Settings
from app.dingtalk import DingTalkTransport
from app.providers import ProviderError, make_provider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", action="store_true", help="Make one small paid text request after authentication succeeds")
    args = parser.parse_args()
    settings = Settings.load()
    report = {}
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.get("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + settings.openrouter_api_key})
        report["openrouter_auth"] = {"http_status": response.status_code, "ok": response.is_success}
    except Exception as exc:
        report["openrouter_auth"] = {"ok": False, "error_type": type(exc).__name__}
    try:
        report["dingtalk_auth"] = {"ok": bool(DingTalkTransport(settings).access_token())}
    except ProviderError as exc:
        report["dingtalk_auth"] = {"ok": False, "error": exc.code}
    except Exception as exc:
        report["dingtalk_auth"] = {"ok": False, "error_type": type(exc).__name__}
    if args.text and report["openrouter_auth"].get("ok"):
        try:
            output = make_provider(replace(settings, max_output_tokens=16, model_timeout_seconds=45)).text([
                {"role": "user", "content": "Connection test. Reply only with OK."}
            ])
            report["text_request"] = {"ok": bool(output.text.strip()), "reply_is_ok": output.text.strip() == "OK", "usage": output.usage}
        except ProviderError as exc:
            report["text_request"] = {"ok": False, "error": exc.code}
        except Exception as exc:
            report["text_request"] = {"ok": False, "error_type": type(exc).__name__}
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "live-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
