# checker.py
import datetime
import os
import random

TARGET_DESC = os.getenv("TARGET_DESC", "春節返鄉車票")


def check_ticket_available() -> bool:
    """
    目前是假邏輯：
    - 每次呼叫印出一次 log
    - 用 random 決定有沒有票（預設 10% 機率 True）

    之後你可以改成：
    - 用 requests 去打台鐵/高鐵查詢頁
    - 用 BeautifulSoup / 字串判斷是否有座位
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Checking tickets for {TARGET_DESC!r} ...")

    prob = float(os.getenv("FAKE_TICKET_PROB", "0.1"))
    has_ticket = random.random() < prob

    print(f"[{now}] result: {'AVAILABLE' if has_ticket else 'NOT_AVAILABLE'}")
    return has_ticket
