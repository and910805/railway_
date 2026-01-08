# checker.py
import datetime
import os
import re

import requests
from bs4 import BeautifulSoup

# 台鐵頁面 / 查詢結果 URL
SEARCH_PAGE_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip121/query"
SEARCH_RESULT_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip119/search"

# 共用 Session（保留 cookie）
_session = requests.Session()


# ========= 工具函式 =========
def _fetch_tokens(session: requests.Session):
    """
    嘗試從搜尋頁抓 _csrf / action-token。
    抓不到就回傳 (None, None)，不要 raise，避免整個 job 掛掉。
    """
    try:
        resp = session.get(
            SEARCH_PAGE_URL,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            },
        )
        print("[checker] GET search page status =", resp.status_code, flush=True)
        resp.raise_for_status()
    except Exception as e:
        print("[checker] 取搜尋頁失敗：", e, flush=True)
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    csrf_input = soup.find("input", {"name": "_csrf"})
    action_token_input = soup.find("input", {"name": "action-token"})

    if not csrf_input or not action_token_input:
        print(
            "[checker] 找不到 _csrf / action-token，先試著不用 token 查詢看看",
            flush=True,
        )
        return None, None

    csrf = csrf_input.get("value") or ""
    action_token = action_token_input.get("value") or ""

    print(
        f"[checker] 抓到 _csrf={csrf[:10]}..., action-token length={len(action_token)}",
        flush=True,
    )
    return csrf, action_token


def _build_payload(task: dict, csrf: str | None, action_token: str | None) -> dict:
    """
    把任務裡的條件組成 POST 表單。
    task 需至少包含：
      ride_date, start_station, end_station, start_time, end_time
    """
    form = {
        "rideDate": task["ride_date"],         # 例: 2026/01/27
        "startStation": task["start_station"], # 例: 1008-台北
        "endStation": task["end_station"],     # 例: 4220-高雄
        "startOrEndTime": "true",              # 固定查出發時間
        "startTime": task["start_time"],       # 例: 06:00
        "endTime": task["end_time"],           # 例: 12:00
        "action-name": "submit_form",
    }

    # 只有真的抓到 token 才帶進去
    if csrf:
        form["_csrf"] = csrf
    if action_token:
        form["action-token"] = action_token

    print("[checker] payload form =", form, flush=True)
    return form


def _parse_seat_count(text: str) -> int | None:
    """
    從「餘票狀態」欄位文字/alt 裡抓出大致座位數。
    範例：
      '>30位'    -> 30
      '10位'     -> 10
      '10~1位'   -> 10 (取最大值)
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


def _find_target_train_has_seat(
    html: str, train_keyword: str, min_seats: int
) -> bool:
    """
    在回傳 HTML 的表格中找符合條件的列。

    - 如果 train_keyword 是空字串 / "*" => 任何一班餘座 >= min_seats 就 True
    - 否則只看「包含 train_keyword 的列」
    """
    soup = BeautifulSoup(html, "html.parser")

    match_all = (not train_keyword) or (train_keyword == "*")

    # 找包含「餘票狀態／車種車次」的表格
    table = None
    for t in soup.find_all("table"):
        txt = t.get_text(" ", strip=True)
        if "餘票狀態" in txt and "車種車次" in txt:
            table = t
            break

    if not table:
        print("[checker] 找不到結果表格（餘票狀態 / 車種車次）", flush=True)
        return False

    rows = table.find_all("tr")
    print(f"[checker] result table rows = {len(rows)}", flush=True)

    for idx, tr in enumerate(rows):
        row_text = tr.get_text(" ", strip=True)

        # 有指定關鍵字時才過濾車次
        if (not match_all) and (train_keyword not in row_text):
            continue

        tds = tr.find_all("td")
        if not tds:
            continue

        # 嘗試抓第 7 欄當餘票欄，失敗再 fallback 找有「位」字的欄位
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

        # seat_text = 文字 + <img> alt/title
        parts = [seat_td.get_text(" ", strip=True)]
        for img in seat_td.find_all("img"):
            alt = img.get("alt") or img.get("title") or ""
            if alt:
                parts.append(alt)
        seat_text = " ".join(p for p in parts if p)

        seat_count = _parse_seat_count(seat_text)
        print(
            f"[checker] row {idx} "
            f"train_keyword={train_keyword!r} match_all={match_all} "
            f"seat_text={seat_text!r}, parsed={seat_count}",
            flush=True,
        )

        if seat_count is not None and seat_count >= min_seats:
            print(
                f"[checker] ✅ 符合條件："
                f"train_keyword={train_keyword!r}, seat_count={seat_count} >= {min_seats}",
                flush=True,
            )
            return True

    print(
        f"[checker] 沒有任何列符合：train_keyword={train_keyword!r}, min_seats={min_seats}",
        flush=True,
    )
    return False


# ========= 給 app.py 用的主函式 =========
def check_task_has_ticket(task: dict) -> bool:
    """
    針對「一個任務」檢查是否有票。

    task 需要的欄位：
      id, description,
      ride_date, start_station, end_station,
      start_time, end_time,
      train_keyword, min_seats
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"[{now}] Check task #{task['id']}: {task['description']} "
        f"({task['ride_date']} {task['start_time']}~{task['end_time']}, "
        f"{task['train_keyword']}, min_seats={task['min_seats']})",
        flush=True,
    )

    # 1) 先抓 token（抓不到也沒關係）
    csrf, action_token = _fetch_tokens(_session)

    # 2) 組查詢表單
    payload = _build_payload(task, csrf, action_token)

    # 3) 送出查詢
    try:
        resp = _session.post(
            SEARCH_RESULT_URL,
            data=payload,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": SEARCH_PAGE_URL,
            },
        )
        print("[checker] POST search status =", resp.status_code, flush=True)
        resp.raise_for_status()
    except Exception as e:
        print("[checker] 查詢失敗：", e, flush=True)
        return False

    # 4) 解析 HTML 看有沒有符合條件的車
    return _find_target_train_has_seat(
        resp.text,
        train_keyword=str(task.get("train_keyword") or "*"),
        min_seats=int(task.get("min_seats", 1)),
    )


# ========= Demo：單一環境變數版（/manual-check 用） =========
def check_ticket_available() -> bool:
    """
    簡易版：用環境變數湊出一個 fake task 來查。
    方便 /manual-check 或本地測試。
    """
    fake_task = {
        "id": 0,
        "description": os.getenv("TARGET_DESC", "環境變數單一任務"),
        "ride_date": os.getenv("TRA_RIDE_DATE", "2026/01/27"),
        "start_station": os.getenv("TRA_START_STATION", "1008-台北"),
        "end_station": os.getenv("TRA_END_STATION", "4220-高雄"),
        "start_time": os.getenv("TRA_START_TIME", "06:00"),
        "end_time": os.getenv("TRA_END_TIME", "12:00"),
        # 不給就看所有車；要指定就設成 "自強135" 類似這樣
        "train_keyword": os.getenv("TRAIN_KEYWORD", "*"),
        "min_seats": int(os.getenv("MIN_SEATS", "2")),
    }
    return check_task_has_ticket(fake_task)
