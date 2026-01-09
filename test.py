# test_run.py
from checker import check_task_has_ticket
import json

# 1. 準備測試資料 (使用臭寶提供的實戰車站代碼)
test_task = {
    "id": 999,
    "ride_date": "2026/01/27",
    "start_station": "1000-臺北",  #
    "end_station": "4400-高雄",    #
    "start_time": "06:00",
    "end_time": "18:00",
    "train_keyword": "*",         # 測試 OR 邏輯：時段內任何車次都查
    "min_seats": 2
}

print("=== 🚀 臭寶專屬：台鐵查票實戰測試開始 ===")
print(f"🔍 測試目標：{test_task['ride_date']} | {test_task['start_station']} -> {test_task['end_station']}")
print(f"⏰ 時段：{test_task['start_time']} ~ {test_task['end_time']}")
print("========================================\n")

# 2. 執行測試
try:
    # 呼叫我們之前優化過的 checker 邏輯
    result = check_task_has_ticket(test_task)
    
    if result:
        print("\n🎉 [測試結果]：恭喜臭寶！成功找到符合條件的票！")
    else:
        print("\n😭 [測試結果]：目前時段內沒有符合張數的票，或被台鐵擋掉。")

except Exception as e:
    print(f"\n❌ [測試崩潰]：程式發生錯誤，快叫臭咘咘來修理！\n錯誤內容：{e}")

print("\n=== 測試結束 ===")