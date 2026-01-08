# checker.py
import datetime
import os
import re

import requests
from bs4 import BeautifulSoup

SEARCH_PAGE_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip121/query"
SEARCH_RESULT_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip119/search"


_session = requests.Session()


def _fetch_tokens():
    """先 GET 搜尋頁，抓 _csrf / action-token"""
    resp = _session.get(
        SEARCH_PAGE_URL,
        timeout=10,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        },
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    csrf_input = soup.find("input", {"name": "_csrf"})
    action_token_input = soup.find("input", {"name": "action-token"})

    if not csrf_input or not action_token_input:
        raise RuntimeError("找不到 _csrf 或 action-token，台鐵頁面可能改版")

    csrf = csrf_input.get("value") or ""
    action_token = action_token_input.get("value") or ""
    return csrf, action_token


def _build_payload(task: dict, csrf: str, action_token: str) -> dict:
    """把任務裡的條件塞成查詢表單"""
    return {
        "_csrf": csrf,
        "action-token": action_token,
        "action-name": "submit_form",
        "rideDate": task["ride_date"],         # 2026/01/27
        "startStation": task["start_station"], # 1008-台北
        "endStation": task["end_station"],     # 4220-高雄
        "startOrEndTime": "true",              # 固定查出發時間
        "startTime": task["start_time"],       # 06:00
        "endTime": task["end_time"],           # 12:00
    }


def _parse_seat_count(text: str) -> int | None:
    """
    從「餘票狀態」欄位文字/alt 字串抓出大概座位數
    例如：
      '>30位' -> 30
      '10~1位' -> 10
      '30位' -> 30
    """
    if not text:
        return None

    text = text.replace(" ", "")
    nums = [int(m.group(1)) for m in re.finditer(r"(\d+)\s*位", text)]
    if nums:
        return max(nums)
    if ">30" in text:
        return 30
    return None


def _find_target_train_has_seat(html: str, train_keyword: str, min_seats: int) -> bool:
    soup = BeautifulSoup(html, "html.parser")

    # 找包含「餘票狀態／車種車次」的表格
    table = None
    for t in soup.find_all("table"):
        text = t.get_text(" ", strip=True)
        if "餘票狀態" in text and "車種車次" in text:
            table = t
            break

    if not table:
        print("[checker] 找不到結果表格（餘票狀態 / 車種車次）", flush=True)
        return False

    rows = table.find_all("tr")
    for idx, tr in enumerate(rows):
        row_text = tr.get_text(" ", strip=True)
        if train_keyword not in row_text:
            continue

        tds = tr.find_all("td")
        if not tds:
            continue

        # 嘗試抓第 7 欄當餘票欄，失敗再 fallback
        seat_td = None
        if len(tds) >= 7:
            seat_td = tds[6]
        else:
            for td in tds:
                if "位" in td.get_text():
                    seat_td = td
                    break
        if not seat_td:
            continue

        # seat_text = 文字 + img alt/title
        parts = [seat_td.get_text(" ", strip=True)]
        for img in seat_td.find_all("img"):
            alt = img.get("alt") or img.get("title") or ""
            if alt:
                parts.append(alt)
        seat_text = " ".join(p for p in parts if p)

        seat_count = _parse_seat_count(seat_text)
        print(
            f"[checker] row {idx} {train_keyword}: seat_text={seat_text!r}, "
            f"parsed={seat_count}",
            flush=True,
        )

        if seat_count is not None and seat_count >= min_seats:
            return True

    return False


def check_task_has_ticket(task: dict) -> bool:
    """
    針對一個任務檢查是否有票：
    - task['ride_date'], ['start_station'], ['end_station'], ['start_time'], ['end_time']
    - task['train_keyword'], task['min_seats']
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"[{now}] Check task #{task['id']}: {task['description']} "
        f"({task['ride_date']} {task['start_time']}~{task['end_time']}, "
        f"{task['train_keyword']}, min_seats={task['min_seats']})",
        flush=True,
    )

    csrf, action_token = _fetch_tokens()
    payload = _build_payload(task, csrf, action_token)

    resp = _session.post(
        SEARCH_RESULT_URL,
        data=payload,
        timeout=10,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": SEARCH_PAGE_URL,
        },
    )
    print("[checker] POST status =", resp.status_code, flush=True)
    resp.raise_for_status()

    return _find_target_train_has_seat(
        resp.text,
        train_keyword=task["train_keyword"],
        min_seats=int(task["min_seats"]),
    )


# 舊版單一任務 API（如果你原本 app 有用，可以先暫存）
def check_ticket_available() -> bool:
    """用環境變數跑一個 demo 版，方便 /manual-check 測試"""
    fake_task = {
        "id": 0,
        "description": os.getenv("TARGET_DESC", "環境變數單一任務"),
        "ride_date": os.getenv("TRA_RIDE_DATE", "2026/01/27"),
        "start_station": os.getenv("TRA_START_STATION", "1008-台北"),
        "end_station": os.getenv("TRA_END_STATION", "4220-高雄"),
        "start_time": os.getenv("TRA_START_TIME", "06:00"),
        "end_time": os.getenv("TRA_END_TIME", "12:00"),
        "train_keyword": os.getenv("TRAIN_KEYWORD", "自強135"),
        "min_seats": int(os.getenv("MIN_SEATS", "2")),
    }
    return check_task_has_ticket(fake_task)
