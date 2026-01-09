import asyncio
import random
import nodriver as uc
from bs4 import BeautifulSoup
import re

# 🚀 根據臭寶實戰偵查的正確網址
SEARCH_PAGE_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip119/queryTime"

def _parse_seat_count(text: str) -> int:
    """解析餘票文字，例如 '>30位' 或 '5位' """
    if not text: return 0
    text = text.replace(" ", "").replace("\n", "").replace("\r", "")
    if ">" in text: return 31 
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else 0

async def _do_check(task: dict) -> bool:
    """使用 nodriver 進行雲端匿蹤查票"""
    # 🚀 雲端部署關鍵：設定 headless=True (無頭模式) 並加入防沙盒參數
    browser = await uc.start(
        headless=True, 
        browser_args=[
            "--no-sandbox", 
            "--disable-setuid-sandbox", 
            "--disable-dev-shm-usage",
            "--disable-gpu"
        ]
    )
    
    try:
        print(f"[checker] 🚀 正在前往官網入口...", flush=True)
        page = await browser.get(SEARCH_PAGE_URL)
        # 增加等待時間，確保雲端載入腳本穩定
        await asyncio.sleep(6) 
        
        # 1. 精準填寫起訖站 (使用 JS 直接賦值，防止載入中斷)
        await page.evaluate(f'document.getElementById("startStation").value = "{task["start_station"]}"')
        await page.evaluate(f'document.getElementById("endStation").value = "{task["end_station"]}"')
        
        # 2. 填寫日期：使用臭寶發現的正確 ID 'calendar1'
        ride_date = task["ride_date"].replace("-", "/")
        await page.evaluate(f'document.getElementById("calendar1").value = "{ride_date}"')
        
        # 3. 設定時間：使用臭寶發現的 ID 'startTime1' 和 'endTime1'
        await page.evaluate(f'document.getElementById("startTime1").value = "{task["start_time"]}"')
        await page.evaluate(f'document.getElementById("endTime1").value = "{task["end_time"]}"')
        
        print(f"[checker] ✅ 雲端校準填寫：{task['start_station']}->{task['end_station']} ({ride_date})", flush=True)

        # 4. 點擊查詢：直接對準臭寶發現的 #searchButton
        await asyncio.sleep(random.uniform(1, 2))
        print("[checker] 🖱️ 對準 #searchButton 執行點擊...", flush=True)
        await page.evaluate('document.getElementById("searchButton").click()')

        # 5. 等待結果 (雲端環境建議多等幾秒讓資料渲染)
        print("[checker] 🔍 正在讀取 1/27 的即時戰報...", flush=True)
        await asyncio.sleep(10) 
        
        html_content = await page.get_content()
        
        # 6. 判斷是否有跳出驗證碼或驗證失敗
        if "驗證未通過" in html_content or "驗證碼驗證失敗" in html_content:
            print("[checker] ❌ 警告：雲端 IP 觸發驗證碼大魔王了！", flush=True)
            return False

        if "車種車次" not in html_content:
            print("[checker] ❌ 查詢結果頁載入失敗，可能查無此時段班次。", flush=True)
            return False

        # 7. 解析所有符合條件的車次
        soup = BeautifulSoup(html_content, "html.parser")
        rows = soup.find_all("tr", class_="trip-column")
        print(f"[checker] 🎉 成功！共找到 {len(rows)} 班車次", flush=True)

        found_any = False
        for row in rows:
            row_text = row.get_text(" ", strip=True)
            train_kw = str(task["train_keyword"])
            # OR 邏輯：不限車次(*) 或 匹配特定車號
            if train_kw == "*" or "".join(filter(str.isdigit, train_kw)) in row_text:
                seat_td = row.find("td", class_="ticketleft")
                if seat_td:
                    seat_span = seat_td.find("span", class_="sr-only")
                    if seat_span:
                        seat_text = seat_span.get_text(strip=True)
                        count = _parse_seat_count(seat_text)
                        print(f"[checker] ✅ 發現：{row_text[:30]} | 餘座：{seat_text}", flush=True)
                        if count >= task.get("min_seats", 1):
                            found_any = True
        return found_any

    except Exception as e:
        print(f"[checker] ❌ 雲端執行異常：{e}", flush=True)
        return False
    finally:
        # 解決 NoneType 報錯：確保 browser 存在且安全關閉
        if 'browser' in locals() and browser:
            try:
                await browser.stop()
            except:
                pass

def check_task_has_ticket(task: dict) -> bool:
    """同步包裝器供 app.py 調用"""
    try:
        loop = uc.loop()
        return loop.run_until_complete(_do_check(task))
    except Exception as e:
        print(f"[系統] 雲端引擎執行失敗：{e}", flush=True)
        return False