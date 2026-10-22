import io
import uuid
import warnings
from pathlib import Path

from PIL import Image

MAX_IMAGE_BYTES = 20_000_000


class Storage:
    def __init__(self, settings):
        self.root = (settings.data_dir / "assets").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, name):
        result = (self.root / name).resolve()
        if not name or result.parent != self.root:
            raise ValueError("非法图片路径")
        return result

    def save(self, data):
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("图片为空或超过 20MB")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in {"PNG", "JPEG", "WEBP"} or im.width * im.height > 25_000_000:
                    raise ValueError("仅支持 PNG/JPEG/WebP，最多 2500 万像素")
                im.load()
                converted = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                output = io.BytesIO()
                converted.save(output, "PNG")
        content = output.getvalue()
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError("标准化后图片超过 20MB，请缩小原图")
        name = f"{uuid.uuid4().hex}.png"
        target = self.resolve(name)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        return name

    def read(self, name):
        return self.resolve(name).read_bytes()

