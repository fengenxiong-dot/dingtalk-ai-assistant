import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    database_url: str = "sqlite:///data/assistant.db"
    data_dir: Path = Path("data")
    provider: str = "mock"
    openrouter_api_key: str = ""
    openrouter_text_model: str = "openai/gpt-4.1-mini"
    openrouter_helper_model: str = "openai/gpt-4.1-mini"
    openrouter_image_model: str = "openai/gpt-image-2"
    admin_token: str = ""
    dev_token: str = ""
    text_base_url: str = ""
    text_api_key: str = ""
    text_model: str = ""
    image_base_url: str = ""
    image_api_key: str = ""
    image_model: str = ""
    image_edit_format: str = "json"
    image_extra: dict = field(default_factory=dict)
    text_extra: dict = field(default_factory=dict)
    model_timeout_seconds: int = 180
    max_output_tokens: int = 1600
    context_byte_budget: int = 8000
    dingtalk_corp_id: str = ""
    dingtalk_client_id: str = ""
    dingtalk_client_secret: str = ""
    dingtalk_robot_code: str = ""
    dingtalk_download_hosts: str = "dingtalk.com,alicdn.com,aliyuncs.com"
    default_text_limit: int = 50
    default_image_limit: int = 5
    daily_credit_limit: int = 500
    text_reserve_fen: int = 0
    image_reserve_fen: int = 0
    daily_budget_fen: int = 0
    asset_retention_days: int = 30
    unlimited_staff_ids: str = ""

    @classmethod
    def load(cls, require_credentials=True):
        load_dotenv()
        values = {}
        defaults = cls()
        for name in cls.__dataclass_fields__:
            key = {"text_extra": "TEXT_EXTRA_JSON", "image_extra": "IMAGE_EXTRA_JSON"}.get(name, name.upper())
            if key in os.environ:
                value = os.environ[key]
                default = getattr(defaults, name)
                values[name] = (int(value) if isinstance(default, int) else
                                Path(value) if isinstance(default, Path) else
                                json.loads(value) if isinstance(default, dict) else value)
        result = cls(**values)
        result.validate(require_credentials=require_credentials)
        return result

    def validate(self, require_credentials=True):
        if self.provider not in {"mock", "openai", "openrouter", "compatible"}:
            raise ValueError("PROVIDER 必须为 mock、openai、openrouter 或 compatible")
        if self.provider == "openrouter":
            if require_credentials and not self.openrouter_api_key.strip():
                raise ValueError("缺少 OPENROUTER_API_KEY，请在本地 .env 填写 OpenRouter 的密钥。")
            for name in ("openrouter_text_model", "openrouter_helper_model", "openrouter_image_model"):
                if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._:+-]+", getattr(self, name)):
                    raise ValueError(f"{name.upper()} 需要完整模型 ID，例如 openai/gpt-4.1-mini")
        elif self.provider != "mock":
            for prefix in ("text", "image"):
                for suffix in ("base_url", "api_key", "model"):
                    if suffix == "api_key" and not require_credentials:
                        continue
                    if not getattr(self, f"{prefix}_{suffix}"):
                        raise ValueError(f"缺少 {prefix.upper()}_{suffix.upper()}")
                if not getattr(self, f"{prefix}_base_url").startswith("https://"):
                    raise ValueError("真实模型服务必须使用 HTTPS")
        if self.context_byte_budget < 4096 or self.max_output_tokens < 1:
            raise ValueError("上下文预算至少 4096 字节，输出上限必须为正")
        if self.image_edit_format not in {"json", "multipart"}:
            raise ValueError("IMAGE_EDIT_FORMAT 必须为 json 或 multipart")
        if not isinstance(self.text_extra, dict) or not isinstance(self.image_extra, dict):
            raise ValueError("TEXT_EXTRA_JSON 和 IMAGE_EXTRA_JSON 必须为 JSON 对象")
        for name in ("default_text_limit", "default_image_limit", "daily_credit_limit", "text_reserve_fen", "image_reserve_fen", "daily_budget_fen"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不得为负")
        if self.daily_budget_fen and (not self.text_reserve_fen or not self.image_reserve_fen):
            raise ValueError("启用估算预算时必须配置文本和图片单任务预留费用")
        if self.model_timeout_seconds < 1 or self.asset_retention_days < 1:
            raise ValueError("超时和图片保留天数必须为正")
        if self.provider != "mock" and self.dev_token:
            raise ValueError("真实模型模式禁止 DEV_TOKEN 模拟身份入口")
