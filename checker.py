import datetime
import os
import re
import requests
from bs4 import BeautifulSoup

# 實戰設定：統一使用 www 網域以維持 Session 一致
TRA_BASE_URL = "https://www.railway.gov.tw"
SEARCH_PAGE_URL = f"{TRA_BASE_URL}/tra-tip-web/tip/tip001/tip121/query"
SEARCH_RESULT_URL = f"{TRA_BASE_URL}/tra-tip-web/tip/tip001/tip119/search"

_session = requests.Session()

def _fetch_tokens(session):
    """
    實戰第一步：抓取台鐵的核心安全令牌
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        resp = session.get(SEARCH_PAGE_URL, timeout=15, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        csrf = soup.find("input", {"name": "_csrf"}).get("value") if soup.find("input", {"name": "_csrf"}) else None
        
        # 實戰經驗：action-token 常常藏在 input 或 script 中
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
    """
    根據臭寶提供的實戰資訊解析：'>30位' 或 '15位'
    """
    if not text: return 0
    text = text.replace(" ", "")
    if ">" in text: return 31 # 表示很多票
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else 0

def check_task_has_ticket(task: dict) -> bool:
    """
    實戰查詢：比對火車車次並抓取 sr-only 裡的座位數
    """
    csrf, action_token = _fetch_tokens(_session)
    if not csrf or not action_token:
        return False

    payload = {
        "rideDate": task["ride_date"].replace("-", "/"),
        "startStation": task["start_station"],
        "endStation": task["end_station"],
        "startOrEndTime": "true",
        "startTime": task["start_time"],
        "endTime": task["end_time"],
        "action-name": "submit_form",
        "_csrf": csrf,
        "action-token": action_token
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": SEARCH_PAGE_URL,
        "Origin": TRA_BASE_URL
    }

    try:
        resp = _session.post(SEARCH_RESULT_URL, data=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, "html.parser")
        # 根據截圖：每一列火車都在 class='trip-column' 的 tr 裡
        rows = soup.find_all("tr", class_="trip-column")
        
        for row in rows:
            row_text = row.get_text(" ", strip=True)
            # 檢查是否為臭寶指定的車次（例如 自強135）
            if task["train_keyword"] in row_text or task["train_keyword"] == "*":
                # 關鍵：找到 class='ticketleft' 的 td 並抓取其中的 'sr-only' span
                seat_td = row.find("td", class_="ticketleft")
                if seat_td:
                    seat_span = seat_td.find("span", class_="sr-only")
                    if seat_span:
                        seat_count = _parse_seat_count(seat_span.get_text(strip=True))
                        print(f"[checker] 找到車次，餘位狀況: {seat_count}", flush=True)
                        if seat_count >= task["min_seats"]:
                            return True
        return False
    except Exception as e:
        print(f"[checker] 實戰查詢失敗: {e}", flush=True)
        return False