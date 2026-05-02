"""
強勢股追蹤 - 一鍵更新腳本
功能：
1. 自動讀取截圖資料夾裡最新的籌碼K線截圖
2. 用Claude AI識別股票清單（代號、名稱、族群）
3. 更新今日收盤價
4. 上傳到GitHub，儀表板自動更新

使用方法：每天雙擊「一鍵更新.bat」即可
"""

import json
import time
import urllib.request
import base64
import os
import glob
from datetime import datetime

# ===== 設定區 =====
GITHUB_REPO   = "charles10roger01/stock-tracker"
STOCKS_FILE   = "stocks.json"
# 截圖資料夾路徑（把籌碼K線截圖存到這裡）
SCREENSHOT_DIR = r"C:\Users\Roger\Desktop\股票截圖"
# ==================

def load_config():
    base = os.path.dirname(os.path.abspath(__file__))
    # GitHub Token
    try:
        with open(os.path.join(base, "config.txt")) as f:
            github_token = f.read().strip()
    except:
        github_token = ""
    # Anthropic API Key
    try:
        with open(os.path.join(base, "anthropic_key.txt")) as f:
            anthropic_key = f.read().strip()
    except:
        anthropic_key = ""
    return github_token, anthropic_key

def today():
    d = datetime.now()
    return f"{d.month}/{d.day}"

def get_latest_screenshot():
    """取得截圖資料夾裡最新的圖片"""
    patterns = ["*.jpg", "*.jpeg", "*.png", "*.JPG", "*.PNG"]
    files = []
    for p in patterns:
        files.extend(glob.glob(os.path.join(SCREENSHOT_DIR, p)))
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def read_screenshot_with_claude(image_path, api_key):
    """用Claude API讀取截圖中的股票清單"""
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")
    
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"
    
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": image_data}
                },
                {
                    "type": "text",
                    "text": "這是台股籌碼K線篩選清單截圖。請讀取所有股票的代號、名稱、細產業分類。只回傳純JSON，不含任何其他文字，格式：{\"stocks\":[{\"code\":\"2417\",\"name\":\"圓剛\",\"sector\":\"電子中游-PC介面卡\"}]}"
                }
            ]
        }]
    }).encode("utf-8")
    
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }
    )
    
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    
    text = data["content"][0]["text"]
    import re
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return json.loads(match.group())
    return None

def fetch_closing_price(code):
    """從證交所抓取收盤價"""
    date_str = datetime.now().strftime("%Y%m%d")
    
    # 上市
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?stockNo={code}&date={date_str}&response=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            if data.get("stat") == "OK" and data.get("data"):
                return float(data["data"][-1][6].replace(",", ""))
    except:
        pass
    
    # 上櫃
    d = datetime.now()
    url2 = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d={d.strftime('%Y/%m/%d')}&stkno={code}&s=0,asc"
    try:
        req2 = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=10) as r:
            data2 = json.loads(r.read())
            if data2.get("aaData"):
                return float(data2["aaData"][-1][2].replace(",", ""))
    except:
        pass
    
    return None

def get_github_file(repo, filename, token):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{filename}",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json", "User-Agent": "stock-tracker"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def update_github_file(repo, filename, content, sha, token, message):
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = json.dumps({"message": message, "content": encoded, "sha": sha}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{filename}",
        data=payload,
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json", "Content-Type": "application/json", "User-Agent": "stock-tracker"},
        method="PUT"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def main():
    print("=" * 50)
    print("  強勢股追蹤 - 一鍵更新")
    print("=" * 50)

    github_token, anthropic_key = load_config()
    
    if not github_token:
        print("\n❌ 找不到 GitHub Token，請確認 config.txt")
        input("\n按 Enter 關閉..."); return
    if not anthropic_key:
        print("\n❌ 找不到 Anthropic API Key，請確認 anthropic_key.txt")
        input("\n按 Enter 關閉..."); return

    # 取得GitHub資料
    print("\n📡 連接 GitHub...")
    try:
        file_info = get_github_file(GITHUB_REPO, STOCKS_FILE, github_token)
        content = base64.b64decode(file_info["content"]).decode("utf-8")
        sha = file_info["sha"]
        data = json.loads(content)
        stocks = data.get("stocks", [])
    except Exception as e:
        print(f"❌ 無法連接 GitHub：{e}")
        input("\n按 Enter 關閉..."); return

    # 讀取最新截圖
    print(f"\n🔍 搜尋截圖資料夾：{SCREENSHOT_DIR}")
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)
        print(f"✅ 已建立截圖資料夾，請把籌碼K線截圖存到這裡")
    
    latest = get_latest_screenshot()
    if latest:
        print(f"📷 找到截圖：{os.path.basename(latest)}")
        print(f"🤖 AI 讀取中...")
        try:
            result = read_screenshot_with_claude(latest, anthropic_key)
            if result and result.get("stocks"):
                new_stocks = result["stocks"]
                added = 0
                for s in new_stocks:
                    if not any(x["code"] == s["code"] for x in stocks):
                        stocks.append({"code": s["code"], "name": s.get("name",""), "sector": s.get("sector",""), "prices": {}, "addedDate": today()})
                        added += 1
                print(f"✅ 讀取到 {len(new_stocks)} 檔，新增 {added} 檔到觀察清單")
            else:
                print("⚠️ 無法讀取截圖內容")
        except Exception as e:
            print(f"⚠️ AI 讀取失敗：{e}")
    else:
        print(f"⚠️ 截圖資料夾是空的，跳過新增股票")

    # 更新收盤價
    today_str = today()
    print(f"\n📅 更新收盤價（{today_str}）...")
    print(f"📋 追蹤股票數：{len(stocks)} 檔\n")
    
    success = 0
    for s in stocks:
        print(f"  抓取 {s['code']} {s.get('name','')}...", end=" ")
        price = fetch_closing_price(s["code"])
        if price:
            if "prices" not in s: s["prices"] = {}
            s["prices"][today_str] = price
            print(f"✅ {price}")
            success += 1
        else:
            print("⚠️ 無法取得")
        time.sleep(0.5)

    # 上傳GitHub
    data["stocks"] = stocks
    data["lastUpdate"] = today_str
    new_content = json.dumps(data, ensure_ascii=False, indent=2)
    
    print(f"\n📤 上傳到 GitHub...")
    try:
        update_github_file(GITHUB_REPO, STOCKS_FILE, new_content, sha, github_token, f"更新 {today_str}")
        print(f"✅ 完成！收盤價成功 {success}/{len(stocks)} 檔")
        print(f"\n🌐 儀表板：https://charles10roger01.github.io/stock-tracker/")
    except Exception as e:
        print(f"❌ 上傳失敗：{e}")

    input("\n按 Enter 關閉...")

if __name__ == "__main__":
    main()
