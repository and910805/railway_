#!/usr/bin/env python3
"""
setup_richmenu.py
One-time utility to create + upload + set default LINE rich menu.

Usage (recommended: run on your local machine):
  export LINE_CHANNEL_ACCESS_TOKEN="YOUR_LONG_LIVED_TOKEN"
  python setup_richmenu.py --image assets/richmenu_app_dark_2500x843.png

Notes:
- Creating rich menu uses https://api.line.me
- Uploading rich menu image uses https://api-data.line.me (different domain)
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests


API_BASE = "https://api.line.me"
API_DATA_BASE = "https://api-data.line.me"


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
    }


def create_rich_menu(token: str, body: Dict[str, Any]) -> str:
    url = f"{API_BASE}/v2/bot/richmenu"
    r = requests.post(url, headers={**_headers(token), "Content-Type": "application/json"}, json=body, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Create rich menu failed: {r.status_code} {r.text}")
    data = r.json()
    rid = data.get("richMenuId")
    if not rid:
        raise RuntimeError(f"Create rich menu succeeded but missing richMenuId: {data}")
    return rid


def upload_rich_menu_image(token: str, rich_menu_id: str, image_path: Path) -> None:
    # IMPORTANT: image upload endpoint is on api-data.line.me
    url = f"{API_DATA_BASE}/v2/bot/richmenu/{rich_menu_id}/content"
    ext = image_path.suffix.lower()
    if ext in (".png",):
        ctype = "image/png"
    elif ext in (".jpg", ".jpeg"):
        ctype = "image/jpeg"
    else:
        raise ValueError("Rich menu image must be .png/.jpg/.jpeg")

    data = image_path.read_bytes()
    r = requests.post(url, headers={**_headers(token), "Content-Type": ctype}, data=data, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Upload image failed: {r.status_code} {r.text}")


def set_default_rich_menu(token: str, rich_menu_id: str) -> None:
    url = f"{API_BASE}/v2/bot/user/all/richmenu/{rich_menu_id}"
    r = requests.post(url, headers=_headers(token), timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Set default rich menu failed: {r.status_code} {r.text}")


def clear_default_rich_menu(token: str) -> None:
    url = f"{API_BASE}/v2/bot/user/all/richmenu"
    r = requests.delete(url, headers=_headers(token), timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Clear default rich menu failed: {r.status_code} {r.text}")


def build_bottom_bar_app_dark(
    *,
    chat_bar_text: str = "功能",
    selected: bool = True,
) -> Dict[str, Any]:
    """
    Layout matches a 2500x843 image split into 5 equal buttons (500px each).
    Actions are Message actions so you don't have to worry about URL percent-encoding.
    """
    h = 843
    w = 2500
    bw = 500

    return {
        "size": {"width": w, "height": h},
        "selected": selected,
        "name": "BottomBar App Dark",
        "chatBarText": chat_bar_text,
        "areas": [
            # 1) 天氣
            {
                "bounds": {"x": 0 * bw, "y": 0, "width": bw, "height": h},
                "action": {"type": "message", "label": "Weather", "text": "天氣"},
            },
            # 2) 吃藥（一鍵回報）
            {
                "bounds": {"x": 1 * bw, "y": 0, "width": bw, "height": h},
                "action": {"type": "message", "label": "Pill", "text": "吃了"},
            },
            # 3) Duolingo help（狀態+指令提示）
            {
                "bounds": {"x": 2 * bw, "y": 0, "width": bw, "height": h},
                "action": {"type": "message", "label": "Duo", "text": "Duolingo狀態"},
            },
            # 4) 相簿（由 bot 回覆儀表板登入連結後導到 gallery）
            {
                "bounds": {"x": 3 * bw, "y": 0, "width": bw, "height": h},
                "action": {"type": "message", "label": "Gallery", "text": "相簿"},
            },
            # 5) 攝影任務
            {
                "bounds": {"x": 4 * bw, "y": 0, "width": bw, "height": h},
                "action": {"type": "message", "label": "PhotoTask", "text": "攝影任務"},
            },
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip(), help="Channel access token")
    ap.add_argument("--image", required=True, help="Path to rich menu image (PNG/JPG)")
    ap.add_argument("--chatbar", default="功能", help="Chat bar text (shown above rich menu)")
    ap.add_argument("--no-selected", action="store_true", help="Create menu with selected=false")
    ap.add_argument("--no-default", action="store_true", help="Do NOT set as default rich menu")
    ap.add_argument("--clear-default-first", action="store_true", help="Clear current default rich menu first")
    args = ap.parse_args()

    token = (args.token or "").strip()
    if not token:
        print("ERROR: missing token. Use --token or env LINE_CHANNEL_ACCESS_TOKEN", file=sys.stderr)
        return 2

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"ERROR: image not found: {image_path}", file=sys.stderr)
        return 2

    body = build_bottom_bar_app_dark(chat_bar_text=args.chatbar, selected=(not args.no_selected))

    print("1) Creating rich menu...")
    rich_menu_id = create_rich_menu(token, body)
    print("   richMenuId =", rich_menu_id)

    print("2) Uploading rich menu image...")
    upload_rich_menu_image(token, rich_menu_id, image_path)
    print("   uploaded.")

    if args.clear_default_first:
        print("3) Clearing current default rich menu...")
        try:
            clear_default_rich_menu(token)
            print("   cleared.")
        except Exception as e:
            print("   (warning) clear default failed:", e)

    if not args.no_default:
        print("4) Setting as default rich menu...")
        set_default_rich_menu(token, rich_menu_id)
        print("   set default OK.")

    print("\nDone.")
    print("richMenuId:", rich_menu_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
