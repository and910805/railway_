import asyncio
import random
import nodriver as uc
from bs4 import BeautifulSoup
import re

# 🚀 搶票核心設定
SEARCH_PAGE_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip119/queryTime"
MY_ID_NUMBER = "A123456789"  # 👈 臭寶（吳芃秀），請在這裡輸入妳的身分證字號

def _parse_seat_count(text: str) -> int:
    if not text: return 0
    text = text.replace(" ", "").replace("\n", "").replace("\r", "")
    if ">" in text: return 31 
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else 0

async def _do_check(task: dict) -> bool:
    # 🚀 地端實戰：headless=False 讓臭寶親眼看著它搶票
    browser = await uc.start(headless=False) 
    try:
        print(f"[checker] 🚀 啟動地端戰機！目標：{task['ride_date']} {task['train_keyword']}", flush=True)
        page = await browser.get(SEARCH_PAGE_URL)
        await asyncio.sleep(4) 
        
        # 1. 填寫查票表單
        await page.evaluate(f'document.getElementById("startStation").value = "{task["start_station"]}"')
        await page.evaluate(f'document.getElementById("endStation").value = "{task["end_station"]}"')
        ride_date = task["ride_date"].replace("-", "/")
        await page.evaluate(f'document.getElementById("calendar1").value = "{ride_date}"')
        await page.evaluate(f'document.getElementById("startTime1").value = "{task["start_time"]}"')
        await page.evaluate(f'document.getElementById("endTime1").value = "{task["end_time"]}"')

        # 2. 點擊查詢
        await asyncio.sleep(1)
        await page.evaluate('document.getElementById("searchButton").click()')

        # 3. 等待結果並偵測
        print("[checker] 🔍 盯盤中... 只要訂票鈕一出現就執行秒殺...", flush=True)
        await asyncio.sleep(6) 
        
        # 4. 偵測特定車次並點擊「訂票」
        train_no = "".join(filter(str.isdigit, str(task["train_keyword"])))
        
        clicked_booking = await page.evaluate(f'''() => {{
            const rows = document.querySelectorAll("tr.trip-column");
            for (let row of rows) {{
                if (row.innerText.includes("{train_no}")) {{
                    const bookBtn = row.querySelector("button.icon-ticket");
                    if (bookBtn) {{
                        bookBtn.click(); // 點擊進入訂票頁面
                        return true;
                    }}
                }}
            }}
            return false;
        }}''')

        if clicked_booking:
            print(f"🔥 [警報] 發現 {train_no} 有票！進入訂票流程！", flush=True)
            
            # 5. 跳轉後自動填寫身分證並點擊最終訂票
            await asyncio.sleep(2) 
            
            await page.evaluate(f'''() => {{
                const pidInput = document.getElementById("pid");
                if (pidInput) {{
                    pidInput.value = "{MY_ID_NUMBER}";
                    // 觸發輸入事件確保網頁讀取到
                    pidInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    
                    // 🚀 既然臭寶說不用驗證碼，那我們就直接點下最終訂票鈕！
                    const submitBtn = document.querySelector(".btn-3d");
                    if (submitBtn) {{
                        submitBtn.click();
                    }}
                }}
            }}''')
            
            print(f"🎊 【大功告成】已幫臭寶自動提交訂票！請確認頁面結果！", flush=True)
            # 成功後保持視窗開啟，讓臭寶確認
            await asyncio.sleep(600) 
            return True
        else:
            print(f"[checker] ℹ️ {train_no} 暫無餘位，持續守候中...", flush=True)
            return False

    except Exception as e:
        print(f"[checker] ❌ 搶票過程出錯: {e}", flush=True)
        return False
    finally:
        # 如果沒點到票才關閉，點到了就要留著給臭寶看
        if not 'clicked_booking' in locals() or not clicked_booking:
            if browser: await browser.stop()

def check_task_has_ticket(task: dict) -> bool:
    try:
        return uc.loop().run_until_complete(_do_check(task))
    except: return False