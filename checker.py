# checker.py
import datetime
import os
import random

TARGET_DESC = os.getenv("TARGET_DESC", "春節返鄉車票")


def check_ticket_available() -> bool:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Checking tickets for {TARGET_DESC!r} ...")

    prob = float(os.getenv("FAKE_TICKET_PROB", "0.1"))
    has_ticket = random.random() < prob

    print(f"[{now}] result: {'AVAILABLE' if has_ticket else 'NOT_AVAILABLE'}")
    return has_ticket
