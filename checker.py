import datetime
import os
import re
import requests
from bs4 import BeautifulSoup

TRA_BASE_URL = "https://www.railway.gov.tw"
SEARCH_PAGE_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip112/gobytime"
SEARCH_RESULT_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip112/querybytime"

_session = requests.Session()


def _fetch_tokens(session: requests.Session):
    """
    進入查詢頁抓 csrf / action-token（台鐵頁面常會變，這裡做最大容錯）
    """
    try:
        resp = session.get(SEARCH_PAGE_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[checker] fetch token page failed: {e}", flush=True)
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")

    csrf = None
    csrf_el = soup.find("input", {"name": "_csrf"})
    if csrf_el and csrf_el.get("value"):
        csrf = csrf_el["value"]

    action_token = None
    action_el = soup.find("input", {"name": "action-token"})
    if action_el and action_el.get("value"):
        action_token = action_el["value"]

    return csrf, action_token


def _build_payload(task: dict, csrf: str | None, action_token: str | None) -> dict:
    form = {
        "rideDate": task["ride_date"].replace("-", "/"),  # 確保格式是 YYYY/MM/DD
        "startStation": task["start_station"],
        "endStation": task["end_station"],
        "startOrEndTime": "true",
        "startTime": task["start_time"],
        "endTime": task["end_time"],
        "action-name": "submit_form",
    }
    if csrf:
        form["_csrf"] = csrf
    if action_token:
        form["action-token"] = action_token
    return form


def _parse_seat_num(text: str):
    text = (text or "").strip()
    if not text:
        return None
    # 常見：>30 或 30+ 或 數字+位
    if ">" in text:
        return 30
    nums = [int(m.group(1)) for m in re.finditer(r"(\d+)\s*位", text)]
    return max(nums) if nums else None


def _pick_result_table(soup: BeautifulSoup):
    candidates = soup.find_all("table")
    for t in candidates:
        txt = t.get_text(" ", strip=True)
        if "車種車次" in txt and any(k in txt for k in ("餘票", "可訂", "訂位")):
            return t
    return None


def _find_target_train_has_seat(html: str, train_keyword: str, min_seats: int) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    table = _pick_result_table(soup)
    if not table:
        txt = soup.get_text(" ", strip=True)
        if "車種車次" in txt and any(k in txt for k in ("餘票", "可訂", "訂位")):
            # 找不到 table，但有關鍵字，先當作可能有
            return True
        return False

    rows = table.find_all("tr")
    for tr in rows[1:]:
        cols = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cols:
            continue

        row_text = " ".join(cols)

        # train_keyword: "*" 表示不限制
        if train_keyword and train_keyword != "*" and train_keyword not in row_text:
            continue

        # 嘗試找座位數
        seat_candidates = []
        for c in cols:
            n = _parse_seat_num(c)
            if n is not None:
                seat_candidates.append(n)

        if seat_candidates and max(seat_candidates) >= min_seats:
            return True

        # 有些表格是「可訂」等字樣
        if any(k in row_text for k in ("可訂", "訂位", "可購", "可買")):
            return True

    return False


def check_task_has_ticket(task: dict) -> bool:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 正在檢查任務 #{task['id']}...", flush=True)

    # 1. 抓取最新 Token
    csrf, action_token = _fetch_tokens(_session)

    # 2. 組裝資料
    payload = _build_payload(task, csrf, action_token)

    # 3. 送出查詢
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; ...bKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": SEARCH_PAGE_URL,
            "Origin": TRA_BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = _session.post(SEARCH_RESULT_URL, data=payload, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[checker] 查詢請求失敗：{e}", flush=True)
        return False

    # 4. 解析結果
    return _find_target_train_has_seat(
        resp.text,
        train_keyword=str(task.get("train_keyword") or "*"),
        min_seats=int(task.get("min_seats", 1))
    )
