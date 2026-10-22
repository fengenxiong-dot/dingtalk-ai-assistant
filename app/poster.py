"""Deterministic mobile recruitment poster; original text stays editable."""
import io
import os
import re
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont


def is_poster(prompt):
    head = prompt.split("\n", 1)[0]
    return bool(re.search(r"校招|招聘", head) and re.search(r"长图|海报", head) and "\n" in prompt)


def font(size):
    choices = [os.environ.get("POSTER_FONT", ""), "C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
    for name in choices:
        if name and Path(name).is_file():
            return ImageFont.truetype(name, size)
    raise ValueError("招聘海报需要中文字体，请配置 POSTER_FONT。")


def render(prompt, logo=None):
    body = prompt.split("\n", 1)[1].strip()
    regular, title = font(28), font(44)
    measure = ImageDraw.Draw(Image.new("RGB", (900, 100)))
    lines = []
    for raw in body.splitlines():
        if "二维码" in raw:
            continue
        line = ""
        for char in raw:
            if measure.textlength(line + char, font=regular) > 760:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
    height = 260 + len(lines) * 46 + 330
    if height > 12000:
        raise ValueError("内容过长，请拆成两张招聘长图。")
    canvas = Image.new("RGB", (900, height), "#f0f5ff")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 900, 210), fill="#1355b5")
    draw.text((65, 80), "校园招聘", font=title, fill="white")
    if logo:
        draw.rounded_rectangle((600, 30, 860, 175), radius=15, fill="white")
        with Image.open(io.BytesIO(logo)) as im:
            im = im.convert("RGBA")
            im.thumbnail((230, 125))
            canvas.paste(im, (610, 40), im)
    y = 250
    for line in lines:
        if re.match(r"^[一二三四五六七八九十]+[、，.]", line):
            draw.rounded_rectangle((55, y - 3, 845, y + 39), radius=6, fill="#dce8fc")
        draw.text((65, y), line, font=regular, fill="#122b50")
        y += 46
    urls = re.findall(r"https://[^\s，。；）)]+", body)
    if urls:
        qr = qrcode.make(urls[0]).convert("RGB").resize((240, 240), Image.Resampling.NEAREST)
        canvas.paste(qr, (65, y + 25))
        draw.text((335, y + 85), "扫码打开投递链接", font=regular, fill="#122b50")
    else:
        draw.text((65, y + 40), "未提供投递链接，未生成二维码", font=regular, fill="#122b50")
    output = io.BytesIO()
    canvas.save(output, "PNG")
    return output.getvalue()
