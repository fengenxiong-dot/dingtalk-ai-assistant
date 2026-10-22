"""Operator check: one low-quality generated image and one edit; these incur API usage."""
import json
from dataclasses import replace

from app.config import Settings
from app.providers import ProviderError, make_provider
from app.storage import Storage


def main():
    settings = Settings.load()
    settings = replace(settings, data_dir=settings.data_dir / "live-image-check", image_extra={"quality":"low", "aspect_ratio":"1:1"}, model_timeout_seconds=150)
    provider, storage = make_provider(settings), Storage(settings)
    report = {}

    def save():
        (settings.data_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        result = provider.image("API integration test: a plain blue circle centered on a white background, no text.")
        path = storage.save(result.image)
        report["generation"] = {"ok":True,"file":path,"bytes":len(result.image),"usage":result.usage}
        save()
        print(json.dumps({"generation":report["generation"]}), flush=True)
    except ProviderError as exc:
        report["generation"] = {"ok":False,"error":exc.code}
        save()
        print(json.dumps(report, ensure_ascii=True), flush=True)
        return
    except Exception as exc:
        report["generation"] = {"ok":False,"error_type":type(exc).__name__}
        save()
        print(json.dumps(report, ensure_ascii=True), flush=True)
        return
    try:
        result = provider.image("Change the blue circle to orange. Keep the white background. No text.", storage.read(path))
        edited_path = storage.save(result.image)
        report["edit"] = {"ok":True,"file":edited_path,"bytes":len(result.image),"usage":result.usage}
    except ProviderError as exc:
        report["edit"] = {"ok":False,"error":exc.code}
    except Exception as exc:
        report["edit"] = {"ok":False,"error_type":type(exc).__name__}
    save()
    print(json.dumps({"edit":report["edit"]}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
