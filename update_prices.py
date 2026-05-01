"""
強勢股追蹤 - 每日收盤價更新腳本
使用方法：每天收盤後（下午2點後）執行一次
執行方式：雙擊 update_prices.bat，或在命令提示字元執行 python update_prices.py
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
import os
import subprocess
import sys

# ===== 設定區 =====
GITHUB_REPO  = "charles10roger01/stock-tracker"
STOCKS_FILE  = "stocks.json"

# 從 config.txt 讀取 token（這個檔案不會上傳到 GitHub）
def load_token():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
    try:
        with open(config_path, "r") as f:
            token = f.read().strip()
            if token:
                return token
    except FileNotFoundError:
        pass
    return ""

GITHUB_TOKEN = load_token()
# ==================

def get_today():
    now = datetime.now()
    return f"{now.month}/{now.day}"

def fetch_closing_price(stock_code):
    """從台灣證交所抓取收盤價"""
    today = datetime.now()
    date_str = today.strftime("%Y%m%d")
    
    # 上市股票（TSE）
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?stockNo={stock_code}&date={date_str}&response=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            if data.get("stat") == "OK" and data.get("data"):
                last_row = data["data"][-1]
                close_price = float(last_row[6].replace(",", ""))
                return close_price
    except:
        pass
    
    # 上櫃股票（OTC）
    url2 = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d={today.strftime('%Y/%m/%d')}&stkno={stock_code}&s=0,asc"
    try:
        req2 = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=10) as r:
            data2 = json.loads(r.read())
            if data2.get("aaData"):
                last_row = data2["aaData"][-1]
                close_price = float(last_row[2].replace(",", ""))
                return close_price
    except:
        pass
    
    return None

def get_github_file():
    """從GitHub取得目前的stocks.json"""
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STOCKS_FILE}"
    req = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "stock-tracker"
        }
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def update_github_file(content, sha):
    """更新GitHub上的stocks.json"""
    import base64
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STOCKS_FILE}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = json.dumps({
        "message": f"更新收盤價 {get_today()}",
        "content": encoded,
        "sha": sha
    }).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "stock-tracker"
        },
        method="PUT"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def main():
    print("=" * 50)
    print("  強勢股追蹤 - 收盤價更新")
    print("=" * 50)

    if not GITHUB_TOKEN:
        print("\n❌ 錯誤：請先在腳本裡填入 GITHUB_TOKEN")
        print("   請參考說明文件取得 GitHub Token")
        input("\n按 Enter 關閉...")
        return

    # 取得GitHub上的資料
    print("\n📡 連接 GitHub...")
    try:
        file_info = get_github_file()
    except Exception as e:
        print(f"❌ 無法連接 GitHub：{e}")
        input("\n按 Enter 關閉...")
        return

    import base64
    content = base64.b64decode(file_info["content"]).decode("utf-8")
    sha = file_info["sha"]
    data = json.loads(content)
    stocks = data.get("stocks", [])

    if not stocks:
        print("⚠️ 觀察清單是空的，請先在儀表板新增股票後匯出 stocks.json 上傳到 GitHub")
        input("\n按 Enter 關閉...")
        return

    today_str = get_today()
    print(f"\n📅 更新日期：{today_str}")
    print(f"📋 追蹤股票數：{len(stocks)} 檔\n")

    success = 0
    fail = 0
    for s in stocks:
        code = s.get("code", "")
        name = s.get("name", code)
        print(f"  抓取 {code} {name}...", end=" ")
        price = fetch_closing_price(code)
        if price:
            if "prices" not in s:
                s["prices"] = {}
            s["prices"][today_str] = price
            print(f"✅ {price}")
            success += 1
        else:
            print("⚠️ 無法取得")
            fail += 1
        time.sleep(0.5)

    data["stocks"] = stocks
    data["lastUpdate"] = today_str
    new_content = json.dumps(data, ensure_ascii=False, indent=2)

    print(f"\n📤 上傳到 GitHub...")
    try:
        update_github_file(new_content, sha)
        print(f"✅ 完成！成功 {success} 檔，失敗 {fail} 檔")
        print(f"\n🌐 請打開儀表板查看：")
        print(f"   https://charles10roger01.github.io/stock-tracker/")
    except Exception as e:
        print(f"❌ 上傳失敗：{e}")

    input("\n按 Enter 關閉...")

if __name__ == "__main__":
    main()
