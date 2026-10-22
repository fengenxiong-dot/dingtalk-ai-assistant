"""Prepare only local development files. Never overwrite existing credentials."""
import importlib.metadata
import secrets
from pathlib import Path


root = Path(__file__).resolve().parents[1]
target = root / ".env"
if not target.exists():
    template = (root / ".env.example").read_text(encoding="utf-8")
    template = template.replace("ADMIN_TOKEN=\n", "ADMIN_TOKEN="+secrets.token_urlsafe(32)+"\n")
    template = template.replace("POSTGRES_PASSWORD=\n", "POSTGRES_PASSWORD="+secrets.token_hex(24)+"\n")
    target.write_text(template, encoding="utf-8")
    print("Created .env in mock mode; secrets are not printed.")
else:
    print("Existing .env preserved.")

if not (root / "requirements.lock").exists():
    packages = sorted(
        d.metadata["Name"]+"=="+d.version
        for d in importlib.metadata.distributions()
        if d.metadata["Name"].lower() not in {"gelanruo-ai", "pip", "setuptools"}
    )
    (root / "requirements.lock").write_text("\n".join(packages)+"\n", encoding="utf-8")
    print("Created dependency snapshot.")
