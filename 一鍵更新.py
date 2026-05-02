"""
強勢股追蹤 - 一鍵更新腳本
功能：
1. 自動讀取截圖資料夾裡最新的籌碼K線截圖
2. 用Claude AI識別股票清單（代號、名稱、族群）
3. 更新今日收盤價
4. 用Selenium截取HiStock布林通道圖
5. 上傳到GitHub，儀表板自動更新
"""

import json, time, urllib.request, base64, os, glob, re, io
from datetime import datetime

GITHUB_REPO    = "charles10roger01/stock-tracker"
STOCKS_FILE    = "stocks.json"
SCREENSHOT_DIR = r"C:\Users\Roger\Desktop\股票截圖"
CHROME_PATH    = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

def load_config():
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base, "config.txt")) as f: github_token = f.read().strip()
    except: github_token = ""
    try:
        with open(os.path.join(base, "anthropic_key.txt")) as f: anthropic_key = f.read().strip()
    except: anthropic_key = ""
    return github_token, anthropic_key

def today():
    d = datetime.now()
    return f"{d.month}/{d.day}"

def get_latest_screenshot():
    files = []
    for p in ["*.jpg","*.jpeg","*.png","*.JPG","*.PNG"]:
        files.extend(glob.glob(os.path.join(SCREENSHOT_DIR, p)))
    return max(files, key=os.path.getmtime) if files else None

def read_screenshot_with_claude(image_path, api_key):
    with open(image_path, "rb") as f: image_data = base64.b64encode(f.read()).decode("utf-8")
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/jpeg" if ext in [".jpg",".jpeg"] else "image/png"
    payload = json.dumps({"model":"claude-sonnet-4-6","max_tokens":1500,"messages":[{"role":"user","content":[
        {"type":"image","source":{"type":"base64","media_type":mime,"data":image_data}},
        {"type":"text","text":"這是台股籌碼K線篩選清單截圖。請讀取所有股票的：代號、名稱、細產業分類、成交價（收盤價）、乖離月線%（最右邊那欄）。只回傳純JSON，不含任何其他文字，格式：{\"stocks\":[{\"code\":\"2417\",\"name\":\"圓剛\",\"sector\":\"電子中游-PC介面卡\",\"price\":47.15,\"ma_deviation\":17.0}]}。如果某欄位看不清楚就填null。"}
    ]}]}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages",data=payload,
        headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
    with urllib.request.urlopen(req,timeout=30) as r: data = json.loads(r.read())
    text = data["content"][0]["text"]
    match = re.search(r'\{[\s\S]*\}', text)
    return json.loads(match.group()) if match else None

def fetch_closing_price(code):
    """抓最近一個交易日的收盤價（自動往前找最多7天）"""
    for days_back in range(7):
        d = datetime.now() - __import__('datetime').timedelta(days=days_back)
        date_str = d.strftime("%Y%m%d")
        date_slash = d.strftime("%Y/%m/%d")
        # 上市
        try:
            req = urllib.request.Request(
                f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?stockNo={code}&date={date_str}&response=json",
                headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=10) as r:
                data = json.loads(r.read())
                if data.get("stat")=="OK" and data.get("data"):
                    last = data["data"][-1]
                    # 確認是當天的資料
                    row_date = last[0].replace("/","")  # 民國年轉換
                    return float(last[6].replace(",","")), d
        except: pass
        # 上櫃
        try:
            req2 = urllib.request.Request(
                f"https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d={date_slash}&stkno={code}&s=0,asc",
                headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req2,timeout=10) as r:
                data2 = json.loads(r.read())
                if data2.get("aaData"):
                    return float(data2["aaData"][-1][2].replace(",","")), d
        except: pass
    return None, None


def get_github_file(repo, filename, token):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{filename}",
        headers={"Authorization":f"token {token}","Accept":"application/vnd.github.v3+json","User-Agent":"stock-tracker"})
    with urllib.request.urlopen(req,timeout=10) as r: return json.loads(r.read())

def update_github_file(repo, filename, content, sha, token, message):
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = json.dumps({"message":message,"content":encoded,"sha":sha}).encode("utf-8")
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{filename}",data=payload,
        headers={"Authorization":f"token {token}","Accept":"application/vnd.github.v3+json","Content-Type":"application/json","User-Agent":"stock-tracker"},method="PUT")
    with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read())

def upload_image_to_github(filename, img_bytes, github_token):
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    try:
        existing = get_github_file(GITHUB_REPO, filename, github_token)
        sha = existing["sha"]
    except: sha = None
    upload_payload = {"message":"更新圖表","content":img_b64}
    if sha: upload_payload["sha"] = sha
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}",
        data=json.dumps(upload_payload).encode("utf-8"),
        headers={"Authorization":f"token {github_token}","Accept":"application/vnd.github.v3+json","Content-Type":"application/json","User-Agent":"stock-tracker"},method="PUT")
    with urllib.request.urlopen(req,timeout=15) as r: r.read()

def screenshot_charts(stocks, github_token):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        from PIL import Image
    except ImportError:
        print("⚠️ 缺少套件，請執行：pip install selenium webdriver-manager pillow")
        return

    print(f"\n📸 開始截取布林通道圖（共 {len(stocks)} 檔）...")

    options = Options()
    options.binary_location = CHROME_PATH
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    except Exception as e:
        print(f"❌ 無法啟動 Chrome：{e}"); return

    success = 0
    for s in stocks:
        code, name = s["code"], s.get("name", s["code"])
        print(f"  截圖 {code} {name}...", end=" ", flush=True)
        try:
            driver.get(f"https://histock.tw/stock/tchart.aspx?no={code}&m=b")
            time.sleep(5)
            png = driver.get_screenshot_as_png()
            img = Image.open(io.BytesIO(png))
            w, h = img.size
            chart = img.crop((80, 430, min(1080, w-200), min(760, h)))
            buf = io.BytesIO()
            chart.save(buf, format="PNG")
            upload_image_to_github(f"charts/{code}.png", buf.getvalue(), github_token)
            print("✅")
            success += 1
        except Exception as e:
            print(f"⚠️ {e}")
        time.sleep(1)

    driver.quit()
    print(f"\n📊 圖表截圖完成：{success}/{len(stocks)} 檔")

def main():
    print("=" * 50)
    print("  強勢股追蹤 - 一鍵更新")
    print("=" * 50)

    github_token, anthropic_key = load_config()
    if not github_token:
        print("\n❌ 找不到 GitHub Token"); input("\n按 Enter 關閉..."); return
    if not anthropic_key:
        print("\n❌ 找不到 Anthropic API Key"); input("\n按 Enter 關閉..."); return

    print("\n📡 連接 GitHub...")
    try:
        file_info = get_github_file(GITHUB_REPO, STOCKS_FILE, github_token)
        content = base64.b64decode(file_info["content"]).decode("utf-8")
        sha = file_info["sha"]
        data = json.loads(content)
        stocks = data.get("stocks", [])
    except Exception as e:
        print(f"❌ 無法連接 GitHub：{e}"); input("\n按 Enter 關閉..."); return

    print(f"\n🔍 搜尋截圖資料夾：{SCREENSHOT_DIR}")
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)

    latest = get_latest_screenshot()
    if latest:
        print(f"📷 找到截圖：{os.path.basename(latest)}\n🤖 AI 讀取中...")
        try:
            result = read_screenshot_with_claude(latest, anthropic_key)
            if result and result.get("stocks"):
                added = 0
                for s in result["stocks"]:
                    existing = next((x for x in stocks if x["code"]==s["code"]), None)
                    if not existing:
                        # 新股票：用截圖的收盤價和乖離月線%反推月線
                        price = s.get("price")
                        ma_dev = s.get("ma_deviation")
                        ma_total = None
                        if price and ma_dev is not None:
                            ma_price = price / (1 + ma_dev / 100)
                            ma_total = ma_price * 20
                            print(f"  {s['code']} 月線反推：{ma_price:.2f}（乖離{ma_dev}%）")
                        new_stock = {
                            "code": s["code"],
                            "name": s.get("name",""),
                            "sector": s.get("sector",""),
                            "side": "bull",
                            "prices": {},
                            "ma_total": ma_total,
                            "addedDate": today()
                        }
                        stocks.append(new_stock)
                        added += 1
                print(f"✅ 讀取到 {len(result['stocks'])} 檔，新增 {added} 檔")
            else: print("⚠️ 無法讀取截圖內容")
        except Exception as e: print(f"⚠️ AI 讀取失敗：{e}")
    else:
        print("⚠️ 截圖資料夾是空的，跳過新增股票")

    today_str = today()
    print(f"\n📅 更新收盤價（{today_str}）...\n📋 追蹤股票數：{len(stocks)} 檔\n")
    success = 0
    for s in stocks:
        print(f"  抓取 {s['code']} {s.get('name','')}...", end=" ")
        price, trade_date = fetch_closing_price(s["code"])
        if price:
            if "prices" not in s: s["prices"] = {}
            date_key = f"{trade_date.month}/{trade_date.day}"
            s["prices"][date_key] = price

            # 滾動更新月線總值
            if s.get("ma_total") is not None:
                old_ma = s["ma_total"] / 20
                s["ma_total"] = s["ma_total"] - old_ma + price
                s["ma"] = s["ma_total"] / 20
            
            print(f"✅ {price} ({date_key}) 月線:{s.get('ma', 'N/A')}")
            success += 1
        else: print("⚠️ 無法取得")
        time.sleep(0.5)

    if stocks:
        screenshot_charts(stocks, github_token)

    data["stocks"] = stocks
    data["lastUpdate"] = today_str
    new_content = json.dumps(data, ensure_ascii=False, indent=2)

    print(f"\n📤 上傳到 GitHub...")
    try:
        update_github_file(GITHUB_REPO, STOCKS_FILE, new_content, sha, github_token, f"更新 {today_str}")
        print(f"✅ 完成！收盤價成功 {success}/{len(stocks)} 檔")
        print(f"\n🌐 儀表板：https://charles10roger01.github.io/stock-tracker/")
    except Exception as e: print(f"❌ 上傳失敗：{e}")

    input("\n按 Enter 關閉...")

if __name__ == "__main__":
    main()
