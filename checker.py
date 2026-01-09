import asyncio
import random
import nodriver as uc
from bs4 import BeautifulSoup
import re

SEARCH_PAGE_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip119/queryTime"

def _parse_seat_count(text: str) -> int:
    if not text: return 0
    text = text.replace(" ", "").replace("\n", "").replace("\r", "")
    if ">" in text: return 31 
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else 0

async def _do_check(task: dict) -> bool:
    # 🚀 Zeabur 雲端部署關鍵：headless=True 與防沙盒參數 
    browser = await uc.start(
        headless=True, 
        browser_args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    )
    try:
        print(f"[checker] 🚀 正在前往官網...", flush=True)
        page = await browser.get(SEARCH_PAGE_URL)
        await asyncio.sleep(6) 
        
        # 使用臭寶偵查到的精確 ID 
        await page.evaluate(f'document.getElementById("startStation").value = "{task["start_station"]}"')
        await page.evaluate(f'document.getElementById("endStation").value = "{task["end_station"]}"')
        
        ride_date = task["ride_date"].replace("-", "/")
        await page.evaluate(f'document.getElementById("calendar1").value = "{ride_date}"')
        
        await page.evaluate(f'document.getElementById("startTime1").value = "{task["start_time"]}"')
        await page.evaluate(f'document.getElementById("endTime1").value = "{task["end_time"]}"')
        print(f"[checker] ✅ 表單填寫完成 ({ride_date})", flush=True)

        await asyncio.sleep(random.uniform(2, 4))
        # 點擊臭寶發現的 #searchButton 
        await page.evaluate('document.getElementById("searchButton").click()')
        await asyncio.sleep(10) 
        
        html_content = await page.get_content()
        if "驗證碼" in html_content:
            print("[checker] ❌ 跳出驗證碼，查票中斷。", flush=True)
            return False

        if "車種車次" not in html_content: return False

        soup = BeautifulSoup(html_content, "html.parser")
        rows = soup.find_all("tr", class_="trip-column")
        print(f"[checker] 🎉 成功！時段內共找到 {len(rows)} 班車", flush=True)

        found_any = False
        for row in rows:
            row_text = row.get_text(" ", strip=True)
            train_kw = str(task["train_keyword"])
            if train_kw == "*" or "".join(filter(str.isdigit, train_kw)) in row_text:
                seat_td = row.find("td", class_="ticketleft")
                if seat_td:
                    seat_span = seat_td.find("span", class_="sr-only")
                    if seat_span:
                        seat_text = seat_span.get_text(strip=True)
                        count = _parse_seat_count(seat_text)
                        print(f"[checker] ✅ 發現：{row_text[:30]} | 餘座：{seat_text}", flush=True)
                        if count >= task.get("min_seats", 1): found_any = True
        return found_any
    finally:
        if 'browser' in locals() and browser:
            try: await browser.stop()
            except: pass

def check_task_has_ticket(task: dict) -> bool:
    try: return uc.loop().run_until_complete(_do_check(task))
    except Exception: return False