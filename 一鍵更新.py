"""
強勢股追蹤 - 一鍵更新腳本
功能：
1. 自動讀取截圖資料夾裡最新的籌碼K線截圖
2. 用Claude AI識別股票清單（含乖離月線%、帶寬）
3. 並行更新今日收盤價
4. 用matplotlib畫折線圖+布林通道
5. 上傳到GitHub
"""

import json, time, urllib.request, base64, os, glob, re, io
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

GITHUB_REPO    = "charles10roger01/stock-tracker"
STOCKS_FILE    = "stocks.json"
SCREENSHOT_DIR = r"C:\Users\Roger\Desktop\股票截圖"

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
    payload = json.dumps({"model":"claude-sonnet-4-6","max_tokens":2000,"messages":[{"role":"user","content":[
        {"type":"image","source":{"type":"base64","media_type":mime,"data":image_data}},
        {"type":"text","text":"這是台股籌碼K線篩選清單截圖。請先看截圖最上方的欄位標題，再根據標題找到對應的數值，讀取每一檔股票的：代號、名稱、細產業分類、成交（收盤價）、帶寬、乖離月線%。注意：乖離月線%和乖離年線%是不同欄位，請確認讀取的是月線而非年線。只回傳純JSON，格式：{\"stocks\":[{\"code\":\"2417\",\"name\":\"圓剛\",\"sector\":\"電子中游-PC介面卡\",\"price\":47.15,\"ma_deviation\":17.0,\"bandwidth\":32.0}]}。看不清楚的欄位填null。"}
    ]}]}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
    with urllib.request.urlopen(req, timeout=30) as r: data = json.loads(r.read())
    text = data["content"][0]["text"]
    match = re.search(r'\{[\s\S]*\}', text)
    return json.loads(match.group()) if match else None

_tpex_cache = {}

def fetch_closing_price(code):
    """用Yahoo Finance抓最近收盤價，自動判斷上市(.TW)或上櫃(.TWO)"""
    import datetime as dt
    for suffix in ['.TW', '.TWO']:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suffix}?interval=1d&range=5d"
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            result = data.get("chart",{}).get("result",[])
            if not result: continue
            timestamps = result[0].get("timestamp",[])
            closes = result[0].get("indicators",{}).get("quote",[{}])[0].get("close",[])
            if not timestamps or not closes: continue
            for ts, price in zip(reversed(timestamps), reversed(closes)):
                if price is not None:
                    trade_date = dt.datetime.fromtimestamp(ts)
                    return round(float(price), 2), trade_date
        except: continue
    return None, None

def fetch_all_prices(stocks):
    """並行抓取所有股票收盤價"""
    results = {}
    def fetch_one(s):
        price, trade_date = fetch_closing_price(s["code"])
        return s["code"], price, trade_date

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_one, s): s for s in stocks}
        for future in as_completed(futures):
            code, price, trade_date = future.result()
            results[code] = (price, trade_date)
    return results


def draw_chart(s):
    """用matplotlib畫折線圖+布林通道"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np
    except ImportError:
        return None

    prices = s.get("prices", {})
    if not prices:
        return None

    sorted_entries = sorted(prices.items(), key=lambda x: (
        int(x[0].split('/')[0]), int(x[0].split('/')[1])
    ))
    dates = [e[0] for e in sorted_entries]
    closes = [float(e[1]) for e in sorted_entries]

    if len(closes) < 1:
        return None

    ma_total = s.get("ma_total")
    bband_upper = s.get("bband_upper")
    bband_lower = s.get("bband_lower")

    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor('#fafafa')
    ax.set_facecolor('#fafafa')

    x = list(range(len(dates)))

    ma_vals = []
    upper_vals = []
    lower_vals = []
    ma_t = s.get("ma_total")
    init_upper = s.get("bband_upper")
    init_lower = s.get("bband_lower")
    bw_pct = None
    if ma_t is not None and init_upper is not None:
        first_ma = ma_t / 20
        if first_ma > 0:
            bw_pct = (init_upper - first_ma) / first_ma

    for i, price in enumerate(closes):
        if ma_t is not None:
            ma_v = ma_t / 20
            ma_vals.append(ma_v)
            if bw_pct is not None:
                upper_vals.append(ma_v * (1 + bw_pct))
                lower_vals.append(ma_v * (1 - bw_pct))
            else:
                upper_vals.append(None)
                lower_vals.append(None)
            if i + 1 < len(closes):
                old_ma = ma_t / 20
                ma_t = ma_t - old_ma + closes[i + 1]
        else:
            ma_vals.append(None)
            upper_vals.append(None)
            lower_vals.append(None)

    if len(closes) == 1:
        ax.scatter(x, closes, color='#1a1a1a', s=50, zorder=3, label=f'Price {closes[-1]:.2f}')
    else:
        ax.plot(x, closes, color='#1a1a1a', linewidth=1.5, marker='o', markersize=3, zorder=3, label=f'Price {closes[-1]:.2f}')

    valid_ma = [(i, v) for i, v in enumerate(ma_vals) if v is not None]
    if valid_ma:
        xi, yi = zip(*valid_ma)
        if len(xi) == 1:
            ax.scatter(xi, yi, color='#2563eb', s=30, zorder=2, label=f'MA20 {yi[0]:.1f}')
        else:
            ax.plot(xi, yi, color='#2563eb', linewidth=1, linestyle='--', alpha=0.7, label=f'MA20')

    valid_upper = [(i, v) for i, v in enumerate(upper_vals) if v is not None]
    if valid_upper:
        xi, yi = zip(*valid_upper)
        if len(xi) == 1:
            ax.scatter(xi, yi, color='#dc2626', s=30, zorder=2, marker='^', label=f'Upper {yi[0]:.1f}')
        else:
            ax.plot(xi, yi, color='#dc2626', linewidth=0.8, linestyle=':', alpha=0.6, label='Upper')

    valid_lower = [(i, v) for i, v in enumerate(lower_vals) if v is not None]
    if valid_lower:
        xi, yi = zip(*valid_lower)
        if len(xi) == 1:
            ax.scatter(xi, yi, color='#16a34a', s=30, zorder=2, marker='v', label=f'Lower {yi[0]:.1f}')
        else:
            ax.plot(xi, yi, color='#16a34a', linewidth=0.8, linestyle=':', alpha=0.6, label='Lower')

    ax.set_xlim(-3, max(len(dates) + 17, 17))
    ax.margins(y=0.15)
    ax.set_title(f"{s['code']} — Price + Bollinger Band", fontsize=11, pad=8)
    ax.set_xticks(x[::max(1, len(x)//6)])
    ax.set_xticklabels([dates[i] for i in x[::max(1, len(x)//6)]], fontsize=7, rotation=30)
    ax.tick_params(axis='y', labelsize=8)
    ax.legend(loc='upper left', fontsize=7, framealpha=0.8)
    ax.grid(axis='y', alpha=0.3, linewidth=0.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='PNG', dpi=120, bbox_inches='tight')
    plt.close()
    return buf.getvalue()

def get_github_file(repo, filename, token):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{filename}",
        headers={"Authorization":f"token {token}","Accept":"application/vnd.github.v3+json","User-Agent":"stock-tracker"})
    with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read())

def update_github_file(repo, filename, content, sha, token, message):
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = json.dumps({"message":message,"content":encoded,"sha":sha}).encode("utf-8")
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{filename}",
        data=payload,
        headers={"Authorization":f"token {token}","Accept":"application/vnd.github.v3+json","Content-Type":"application/json","User-Agent":"stock-tracker"},
        method="PUT")
    with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read())

def upload_image_to_github(filename, img_bytes, github_token):
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    try:
        existing = get_github_file(GITHUB_REPO, filename, github_token)
        sha = existing["sha"]
    except: sha = None
    upload_payload = {"message":f"更新圖表","content":img_b64}
    if sha: upload_payload["sha"] = sha
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}",
        data=json.dumps(upload_payload).encode("utf-8"),
        headers={"Authorization":f"token {github_token}","Accept":"application/vnd.github.v3+json","Content-Type":"application/json","User-Agent":"stock-tracker"},
        method="PUT")
    with urllib.request.urlopen(req, timeout=15) as r: r.read()

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
                updated = 0
                for s in result["stocks"]:
                    existing = next((x for x in stocks if x["code"]==s["code"]), None)
                    price = s.get("price")
                    ma_dev = s.get("ma_deviation")
                    bandwidth = s.get("bandwidth")

                    if not existing:
                        ma_total = None
                        bband_upper = None
                        bband_lower = None
                        if price and ma_dev is not None:
                            ma_price = price / (1 + ma_dev / 100)
                            ma_total = ma_price * 20
                            if bandwidth is not None:
                                bband_upper = ma_price * (1 + bandwidth / 200)
                                bband_lower = ma_price * (1 - bandwidth / 200)
                            upper_str = f"{bband_upper:.2f}" if bband_upper else "N/A"
                            lower_str = f"{bband_lower:.2f}" if bband_lower else "N/A"
                            print(f"  新增 {s['code']} 月線:{ma_price:.2f} 上軌:{upper_str} 下軌:{lower_str}")
                        stocks.append({
                            "code": s["code"], "name": s.get("name",""),
                            "sector": s.get("sector",""), "side": "bull",
                            "prices": {}, "ma_total": ma_total,
                            "bband_upper": bband_upper, "bband_lower": bband_lower,
                            "addedDate": today()
                        })
                        added += 1
                    else:
                        if bandwidth is not None and existing.get("ma_total") is not None:
                            ma_price = existing["ma_total"] / 20
                            existing["bband_upper"] = round(ma_price * (1 + bandwidth / 200), 2)
                            existing["bband_lower"] = round(ma_price * (1 - bandwidth / 200), 2)
                            updated += 1
                print(f"✅ 讀取到 {len(result['stocks'])} 檔，新增 {added} 檔，更新帶寬 {updated} 檔")
            else: print("⚠️ 無法讀取截圖內容")
        except Exception as e: print(f"⚠️ AI 讀取失敗：{e}")
    else:
        print("⚠️ 截圖資料夾是空的，跳過新增股票")

    today_str = today()
    print(f"\n📅 並行更新收盤價（{today_str}）...\n📋 追蹤股票數：{len(stocks)} 檔")
    t0 = time.time()
    price_results = fetch_all_prices(stocks)
    success = 0
    for s in stocks:
        price, trade_date = price_results.get(s["code"], (None, None))
        if price:
            if "prices" not in s: s["prices"] = {}
            date_key = f"{trade_date.month}/{trade_date.day}"
            if date_key not in s["prices"]:
                s["prices"][date_key] = price
                if s.get("ma_total") is not None:
                    old_ma = s["ma_total"] / 20
                    s["ma_total"] = s["ma_total"] - old_ma + price
                    s["ma"] = round(s["ma_total"] / 20, 2)
            print(f"  ✅ {s['code']} {s.get('name','')} {price} ({date_key}) 月線:{s.get('ma','N/A')}")
            success += 1
        else:
            print(f"  ⚠️ {s['code']} {s.get('name','')} 無法取得")
    print(f"\n⏱️ 收盤價更新完成，耗時 {time.time()-t0:.1f} 秒，成功 {success}/{len(stocks)} 檔")


    print(f"\n📊 開始畫布林通道圖...")
    chart_success = 0
    failed = []
    for s in stocks:
        print(f"  畫圖 {s['code']} {s.get('name','')}...", end=" ", flush=True)
        try:
            img_bytes = draw_chart(s)
            if img_bytes:
                upload_image_to_github(f"charts/{s['code']}.png", img_bytes, github_token)
                print("✅")
                chart_success += 1
            else:
                print("⚠️ 資料不足")
        except Exception as e:
            print(f"⚠️ {e}，稍後重試")
            failed.append(s)

    if failed:
        print(f"\n🔄 重試 {len(failed)} 檔失敗的圖表...")
        time.sleep(5)
        for s in failed:
            print(f"  重試 {s['code']} {s.get('name','')}...", end=" ", flush=True)
            try:
                img_bytes = draw_chart(s)
                if img_bytes:
                    upload_image_to_github(f"charts/{s['code']}.png", img_bytes, github_token)
                    print("✅")
                    chart_success += 1
                else:
                    print("⚠️ 資料不足")
            except Exception as e:
                print(f"❌ {e}")
    print(f"📊 圖表完成：{chart_success}/{len(stocks)} 檔")

    data["stocks"] = stocks
    data["lastUpdate"] = today_str
    new_content = json.dumps(data, ensure_ascii=False, indent=2)
    print(f"\n📤 上傳到 GitHub...")
    try:
        update_github_file(GITHUB_REPO, STOCKS_FILE, new_content, sha, github_token, f"更新 {today_str}")
        print(f"✅ 完成！")
        print(f"\n🌐 儀表板：https://charles10roger01.github.io/stock-tracker/")
    except Exception as e: print(f"❌ 上傳失敗：{e}")

    input("\n按 Enter 關閉...")

if __name__ == "__main__":
    main()
