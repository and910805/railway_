import datetime
import os
import re
import requests
from bs4 import BeautifulSoup

# ====== Base URL 設定 ======
# 根據臭寶提供的資料，統一使用 www 網域以確保 Cookie 與 Token 一致
TRA_BASE_URL = "https://www.railway.gov.tw"

SEARCH_PAGE_URL = f"{TRA_BASE_URL}/tra-tip-web/tip/tip001/tip121/query"
SEARCH_RESULT_URL = f"{TRA_BASE_URL}/tra-tip-web/tip/tip001/tip119/search"

# 共用 Session
_session = requests.Session()

# ========= 工具函式 =========
def _fetch_tokens(session: requests.Session):
    """
    從搜尋頁抓取 _csrf 和長度超長的 action-token。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    }
    try:
        resp = session.get(SEARCH_PAGE_URL, timeout=15, headers=headers)
        print(f"[checker] GET 搜尋頁狀態: {resp.status_code}", flush=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"[checker] 取搜尋頁失敗：{e}", flush=True)
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    
    # 1. 抓取 _csrf
    csrf_input = soup.find("input", {"name": "_csrf"})
    csrf = csrf_input.get("value") if csrf_input else None
    
    # 2. 抓取 action-token (優先找 input，找不到就用正規表示法找 script)
    action_token = None
    at_input = soup.find("input", {"name": "action-token"})
    if at_input:
        action_token = at_input.get("value")
    
    if not action_token:
        # 備用方案：從 script 標籤中找尋可能藏起來的 token
        token_match = re.search(r'action-token["\']\s*[:=]\s*["\']([^"\']+)', resp.text)
        if token_match:
            action_token = token_match.group(1)

    if not csrf or not action_token:
        print("[checker] ⚠️ 警告：Token 抓取不完整，台鐵可能會拒絕查詢", flush=True)
    else:
        print(f"[checker] ✅ 成功抓到 Token (CSRF: {csrf[:8]}..., AT長度: {len(action_token)})", flush=True)

    return csrf, action_token

def _build_payload(task: dict, csrf: str | None, action_token: str | None) -> dict:
    form = {
        "rideDate": task["ride_date"].replace("-", "/"), # 確保格式是 YYYY/MM/DD
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

def _parse_seat_count(text: str) -> int | None:
    if not text: return None
    text = text.replace(" ", "")
    # 處理「>30位」或「10位」等格式
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
        # 如果沒找到表格，印出部分 HTML 方便臭咘咘除錯
        print(f"[checker] ❌ 找不到結果表格，可能被導向錯誤頁面。HTML片段：{html[:300]}", flush=True)
        return False

    rows = table.find_all("tr")
    match_all = (not train_keyword) or (train_keyword == "*")

    for tr in rows:
        row_text = tr.get_text(" ", strip=True)
        if not row_text or (not match_all and train_keyword not in row_text):
            continue

        tds = tr.find_all("td")
        if len(tds) < 7: continue

        # 通常餘座在第 7 欄 (index 6)，或找含有「位」字的欄位
        seat_td = tds[6]
        seat_text = seat_td.get_text(" ", strip=True)
        # 加上 img alt (台鐵有時用圖片顯示餘座狀態)
        for img in seat_td.find_all("img"):
            alt = img.get("alt") or img.get("title") or ""
            seat_text += f" {alt}"

        seat_count = _parse_seat_count(seat_text)
        if seat_count is not None and seat_count >= min_seats:
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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