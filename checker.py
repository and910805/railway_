import datetime
import os
import re
import requests
from bs4 import BeautifulSoup

TRA_BASE_URL = "https://www.railway.gov.tw"
SEARCH_PAGE_URL = f"{TRA_BASE_URL}/tra-tip-web/tip/tip001/tip121/query"
SEARCH_RESULT_URL = f"{TRA_BASE_URL}/tra-tip-web/tip/tip001/tip119/search"

_session = requests.Session()

def _fetch_tokens(session):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        resp = session.get(SEARCH_PAGE_URL, timeout=15, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        csrf = soup.find("input", {"name": "_csrf"}).get("value") if soup.find("input", {"name": "_csrf"}) else None
        at_input = soup.find("input", {"name": "action-token"})
        action_token = at_input.get("value") if at_input else None
        if not action_token:
            match = re.search(r'action-token["\']\s*[:=]\s*["\']([^"\']+)', resp.text)
            action_token = match.group(1) if match else None
        return csrf, action_token
    except Exception as e:
        print(f"[checker] 抓取 Token 失敗: {e}", flush=True)
        return None, None

def _parse_seat_count(text: str) -> int:
    """解析 sr-only 裡的文字，例如 '>30位' 或 '5位' """
    if not text: return 0
    text = text.replace(" ", "").replace("\n", "").replace("\r", "")
    if ">" in text: return 31  # 代表票很多
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else 0

def check_task_has_ticket(task: dict) -> bool:
    csrf, action_token = _fetch_tokens(_session)
    if not csrf or not action_token: return False

    payload = {
        "rideDate": task["ride_date"].replace("-", "/"),
        "startStation": task["start_station"],
        "end_station": task["end_station"], # 修正：這裡要跟 task 裡的 key 對應
        "startOrEndTime": "true",
        "startTime": task["start_time"],
        "endTime": task["end_time"],
        "action-name": "submit_form",
        "_csrf": csrf,
        "action-token": action_token
    }

    # 針對台鐵的車站名稱修正
    payload["startStation"] = task["start_station"].replace("台北", "臺北")
    payload["endStation"] = task["end_station"].replace("台北", "臺北")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": SEARCH_PAGE_URL,
        "Origin": TRA_BASE_URL
    }

    try:
        resp = _session.post(SEARCH_RESULT_URL, data=payload, headers=headers, timeout=15)
        if "車種車次" not in resp.text:
            print(f"[DEBUG] ❌ 沒看到結果表格，可能被擋或查無此班。HTML前段：{resp.text[:200]}", flush=True)
            return False
        
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.find_all("tr", class_="trip-column") #
        
        print(f"[DEBUG] 🔍 任務 #{task['id']} 在該時段找到 {len(rows)} 班列車", flush=True)

        for row in rows:
            row_text = row.get_text(" ", strip=True)
            
            # OR 邏輯實作：
            # 1. 如果關鍵字是 '*'，表示時段內有任何車有票都行
            # 2. 如果有關鍵字，則必須包含該車次（只比對數字避免 臺/台 影響）
            train_keyword = str(task["train_keyword"])
            target_no = "".join(filter(str.isdigit, train_keyword))
            
            is_match = False
            if train_keyword == "*" or not train_keyword:
                is_match = True
            elif target_no and target_no in row_text:
                is_match = True
            elif train_keyword in row_text:
                is_match = True

            if is_match:
                seat_td = row.find("td", class_="ticketleft") #
                if seat_td:
                    # 抓取 sr-only 裡的隱藏文字
                    seat_span = seat_td.find("span", class_="sr-only")
                    if seat_span:
                        seat_text = seat_span.get_text(strip=True)
                        count = _parse_seat_count(seat_text)
                        print(f"[DEBUG] 班次匹配成功: {row_text[:30]}... 餘位: {seat_text} ({count})", flush=True)
                        if count >= task["min_seats"]:
                            return True
        return False
    except Exception as e:
        print(f"[DEBUG] 查詢發生錯誤: {e}", flush=True)
        return False