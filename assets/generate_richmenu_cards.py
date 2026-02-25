#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


W, H = 2500, 1686
COLS, ROWS = 3, 2
GAP = 34
PADDING_X = 48
PADDING_Y = 44
CARD_RADIUS = 34


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []
    if bold:
        candidates += [
            r"C:\Windows\Fonts\msjhbd.ttc",
            r"C:\Windows\Fonts\jhbd.ttc",
        ]
    candidates += [
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\jh.ttc",
        r"C:\Windows\Fonts\YuGothB.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def round_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def center_text(draw: ImageDraw.ImageDraw, box, text: str, font, fill):
    x1, y1, x2, y2 = box
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=8, align="center")
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = x1 + (x2 - x1 - tw) / 2
    y = y1 + (y2 - y1 - th) / 2
    draw.multiline_text((x, y), text, font=font, fill=fill, spacing=8, align="center")


def make_image() -> Image.Image:
    img = Image.new("RGB", (W, H), "#dfe9fb")
    draw = ImageDraw.Draw(img)

    # Background gradient-ish bands + soft shapes
    for i in range(H):
        t = i / max(1, H - 1)
        r = int(219 + (138 - 219) * t)
        g = int(236 + (184 - 236) * t)
        b = int(255 + (246 - 255) * t)
        draw.line([(0, i), (W, i)], fill=(r, g, b))

    draw.ellipse((-240, 220, 660, 1120), fill=(255, 255, 255))
    draw.ellipse((1680, 60, 2740, 980), fill=(193, 224, 255))
    draw.ellipse((1540, 980, 2780, 2060), fill=(162, 206, 251))

    title_font = load_font(92, bold=True)
    sub_font = load_font(44)
    card_title_font = load_font(88, bold=True)
    card_sub_font = load_font(42)

    # Top header panel
    header_box = (60, 40, W - 60, 250)
    round_rect(draw, header_box, radius=42, fill=(255, 255, 255), outline=(217, 227, 241), width=3)
    draw.text((120, 86), "提醒快捷選單", font=title_font, fill=(17, 43, 76))
    draw.text((124, 170), "點一下就送出指令給 LINE Bot", font=sub_font, fill=(83, 107, 136))

    cards = [
        {"title": "Duolingo已玩", "subtitle": "今天不再提醒", "bg": "#0b2a59", "line": "#49d17d", "icon": "D"},
        {"title": "吃了", "subtitle": "一鍵回報完成", "bg": "#06285f", "line": "#4cc9ff", "icon": "P"},
        {"title": "小蛋回了", "subtitle": "今日暫停小蛋提醒", "bg": "#072952", "line": "#ffd45e", "icon": "蛋"},
        {"title": "Duolingo狀態", "subtitle": "查看提醒狀態", "bg": "#07224a", "line": "#94f2b8", "icon": "狀"},
        {"title": "小蛋狀態", "subtitle": "看今天是否已回", "bg": "#082246", "line": "#ff9db0", "icon": "蛋"},
        {"title": "儀表板", "subtitle": "打開功能頁面", "bg": "#062041", "line": "#9fe0ff", "icon": "表"},
    ]

    tile_w = (W - PADDING_X * 2 - GAP * (COLS - 1)) // COLS
    tile_h = (H - 320 - PADDING_Y - GAP * (ROWS - 1)) // ROWS
    start_y = 300

    for idx, c in enumerate(cards):
        row = idx // COLS
        col = idx % COLS
        x1 = PADDING_X + col * (tile_w + GAP)
        y1 = start_y + row * (tile_h + GAP)
        x2 = x1 + tile_w
        y2 = y1 + tile_h

        round_rect(draw, (x1, y1, x2, y2), radius=CARD_RADIUS, fill=c["bg"], outline=(173, 195, 228), width=3)
        # top cap
        round_rect(draw, (x1 + 18, y1 + 16, x2 - 18, y1 + 90), radius=20, fill=(247, 250, 255))

        # icon circle
        cx, cy = x1 + tile_w // 2, y1 + 165
        draw.ellipse((cx - 78, cy - 78, cx + 78, cy + 78), fill=(236, 244, 255), outline=(146, 186, 236), width=6)
        icon_font = load_font(78, bold=True)
        ib = draw.textbbox((0, 0), c["icon"], font=icon_font)
        draw.text((cx - (ib[2]-ib[0])/2, cy - (ib[3]-ib[1])/2 - 6), c["icon"], font=icon_font, fill=(77, 123, 184))

        center_text(draw, (x1 + 26, y1 + 240, x2 - 26, y1 + 380), c["title"], card_title_font, (247, 250, 255))
        center_text(draw, (x1 + 36, y1 + 380, x2 - 36, y1 + 465), c["subtitle"], card_sub_font, (187, 207, 232))

        # progress lines near bottom (visual style similar to screenshot)
        line_y = y2 - 42
        draw.rounded_rectangle((x1 + 24, line_y, x2 - 24, line_y + 8), radius=4, fill=(227, 233, 244))
        draw.rounded_rectangle((x1 + 24, line_y, x1 + int(tile_w * 0.65), line_y + 8), radius=4, fill=c["line"])
        draw.ellipse((x2 - 42, line_y - 8, x2 - 20, line_y + 14), fill=c["line"], outline=(255, 255, 255), width=2)

    return img


def main() -> None:
    out = Path(__file__).with_name("richmenu_reminders_6grid_2500x1686.png")
    img = make_image()
    img.save(out, format="PNG", optimize=True)
    print(out)
    print(img.size)


if __name__ == "__main__":
    main()

