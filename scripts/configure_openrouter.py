"""Switch the local model profile without touching DingTalk or direct OpenAI credentials."""
from pathlib import Path

from dotenv import dotenv_values, set_key


def configure(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("请先创建本地 .env")
    original = dotenv_values(path)
    updates = {"PROVIDER": "openrouter"}
    defaults = {"OPENROUTER_API_KEY": "", "OPENROUTER_TEXT_MODEL": "openai/gpt-4.1-mini", "OPENROUTER_IMAGE_MODEL": "openai/gpt-image-2"}
    for key, value in defaults.items():
        if key not in original:
            updates[key] = value
    for key, value in updates.items():
        set_key(path, key, value, quote_mode="never")
    after = dotenv_values(path)
    if any(after.get(k) != v for k, v in original.items() if k != "PROVIDER"):
        raise RuntimeError("其他配置发生变化，请检查文件；未输出任何密钥")
    return bool(after.get("OPENROUTER_API_KEY"))


if __name__ == "__main__":
    configured = configure(Path(__file__).resolve().parents[1] / ".env")
    print("OpenRouter profile selected; other configuration preserved.")
    print("OPENROUTER_API_KEY configured:", configured)
