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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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

def format_full_date(d):
    return f"{d.year}/{d.month}/{d.day}"

def format_short_date(d):
    return f"{d.month}/{d.day}"

def parse_date_key(s):
    parts = [int(p) for p in s.split('/')]
    if len(parts) == 3:
        return datetime(parts[0], parts[1], parts[2])
    if len(parts) == 2:
        return datetime(datetime.now().year, parts[0], parts[1])
    raise ValueError(f"Bad date format: {s}")

def get_screenshot_date(image_path):
    name = os.path.basename(image_path)
    patterns = [
        r'(\d{4})[-_/\.](\d{1,2})[-_/\.](\d{1,2})',
        r'(?<!\d)(\d{1,2})[-_/\.](\d{1,2})(?!\d)',
    ]
    for pattern in patterns:
        match = re.search(pattern, name)
        if not match:
            continue
        parts = [int(p) for p in match.groups()]
        if len(parts) == 3:
            return datetime(parts[0], parts[1], parts[2])
        return datetime(datetime.now().year, parts[0], parts[1])
    raise ValueError(f"截圖檔名找不到日期：{name}")

def valid_price(price):
    try:
        return price is not None and float(price) > 0
    except (TypeError, ValueError):
        return False

def init_ma_and_bands(stock, price, ma_dev, bandwidth):
    if not valid_price(price) or ma_dev is None:
        return
    ma_price = float(price) / (1 + float(ma_dev) / 100)
    stock["ma_total"] = ma_price * 20
    stock["ma"] = round(ma_price, 2)
    if bandwidth is not None:
        stock["bband_upper"] = round(ma_price * (1 + float(bandwidth) / 200), 2)
        stock["bband_lower"] = round(ma_price * (1 - float(bandwidth) / 200), 2)

def add_price(stock, date_key, price, roll_ma=True):
    if not valid_price(price):
        return False
    if "prices" not in stock:
        stock["prices"] = {}
    if date_key in stock["prices"]:
        return False
    stock["prices"][date_key] = round(float(price), 2)
    if roll_ma and stock.get("ma_total") is not None:
        old_ma = stock["ma_total"] / 20
        stock["ma_total"] = stock["ma_total"] - old_ma + float(price)
        stock["ma"] = round(stock["ma_total"] / 20, 2)
    return True

def replace_price(stock, date_key, price, adjust_ma=True):
    """Replace an existing price and correct the stored rolling total."""
    if not valid_price(price):
        return False
    prices = stock.setdefault("prices", {})
    new_price = round(float(price), 2)
    old_price = prices.get(date_key)
    if old_price is None:
        return add_price(stock, date_key, new_price, roll_ma=adjust_ma)
    old_price = float(old_price)
    if abs(old_price - new_price) < 0.005:
        return False
    prices[date_key] = new_price
    if adjust_ma and stock.get("ma_total") is not None:
        stock["ma_total"] = float(stock["ma_total"]) + new_price - old_price
        stock["ma"] = round(stock["ma_total"] / 20, 2)
    return True

def remove_prices_after(stock, cutoff_dt):
    """Remove impossible future prices and undo their rolling-MA updates."""
    prices = stock.get("prices", {})
    future_keys = sorted(
        [k for k in prices if parse_date_key(k) > cutoff_dt],
        key=parse_date_key,
        reverse=True,
    )
    if not future_keys:
        return 0

    old_ma = float(stock["ma_total"]) / 20 if stock.get("ma_total") is not None else None
    upper_factor = None
    lower_factor = None
    if old_ma and stock.get("bband_upper") is not None:
        upper_factor = float(stock["bband_upper"]) / old_ma
    if old_ma and stock.get("bband_lower") is not None:
        lower_factor = float(stock["bband_lower"]) / old_ma

    for date_key in future_keys:
        bad_price = float(prices.pop(date_key))
        if stock.get("ma_total") is not None:
            # Inverse of: new_total = old_total - old_total / 20 + price
            stock["ma_total"] = (float(stock["ma_total"]) - bad_price) * 20 / 19

    if stock.get("ma_total") is not None:
        new_ma = float(stock["ma_total"]) / 20
        stock["ma"] = round(new_ma, 2)
        if upper_factor is not None:
            stock["bband_upper"] = round(new_ma * upper_factor, 2)
        if lower_factor is not None:
            stock["bband_lower"] = round(new_ma * lower_factor, 2)
    return len(future_keys)

def latest_price_date(stock):
    prices = stock.get("prices", {})
    if not prices:
        return None
    return max((parse_date_key(k) for k in prices.keys()), default=None)

def get_latest_screenshot():
    files = []
    for p in ["*.jpg","*.jpeg","*.png","*.JPG","*.PNG"]:
        files.extend(glob.glob(os.path.join(SCREENSHOT_DIR, p)))
    dated_files = []
    for path in files:
        try:
            dated_files.append((get_screenshot_date(path), os.path.getmtime(path), path))
        except ValueError:
            continue
    return max(dated_files)[2] if dated_files else None

def read_screenshot_with_claude(image_path, api_key):
    with open(image_path, "rb") as f: image_data = base64.b64encode(f.read()).decode("utf-8")
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/jpeg" if ext in [".jpg",".jpeg"] else "image/png"
    prompt = (
        "This is a Taiwan stock screener screenshot. The columns from left to right are: "
        "code, name, detailed industry, day-trade flag, traded/close price, change %, volume, blank column, rank, "
        "consecutive count, consecutive volume, volume ratio, monthly MA slope, main1, main5, main10, "
        "bandwidth, deviation from yearly MA %, dividend yield %, deviation from monthly MA %. "
        "Read each stock row and return: code, name, detailed industry as sector, traded/close price as price, "
        "bandwidth, and deviation from monthly MA % as ma_deviation. "
        "The monthly MA deviation is the rightmost column, immediately after dividend yield %. "
        "Do not confuse it with deviation from yearly MA %. "
        "Return pure JSON only, in this exact shape: "
        "{\"stocks\":[{\"code\":\"2417\",\"name\":\"Yuan High-Tech\",\"sector\":\"Electronics-PC interface card\","
        "\"price\":47.15,\"ma_deviation\":17.0,\"bandwidth\":32.0}]}. "
        "Use null only when a value is truly unreadable."
    )
    payload = json.dumps({"model":"claude-sonnet-4-6","max_tokens":4000,"messages":[{"role":"user","content":[
        {"type":"image","source":{"type":"base64","media_type":mime,"data":image_data}},
        {"type":"text","text":prompt}
    ]}]}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
    with urllib.request.urlopen(req, timeout=30) as r: data = json.loads(r.read())
    text = data["content"][0]["text"]
    match = re.search(r'\{[\s\S]*\}', text)
    result = json.loads(match.group()) if match else None
    if result and any(s.get("ma_deviation") is None for s in result.get("stocks", [])):
        print("  Warning: some monthly MA deviations were missing; retrying once...")
        req2 = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
        with urllib.request.urlopen(req2, timeout=30) as r2: data2 = json.loads(r2.read())
        text2 = data2["content"][0]["text"]
        match2 = re.search(r'\{[\s\S]*\}', text2)
        result2 = json.loads(match2.group()) if match2 else None
        if result2:
            result = result2
    return result

_tpex_cache = {}

def fetch_closing_prices(code):
    """Fetch timestamped daily closes from Yahoo Finance."""
    import datetime as dt
    for suffix in ['.TW', '.TWO']:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suffix}?interval=1d&range=10d"
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            result = data.get("chart",{}).get("result",[])
            if not result: continue
            meta = result[0].get("meta", {})
            try:
                exchange_tz = ZoneInfo(meta.get("exchangeTimezoneName") or "Asia/Taipei")
            except Exception:
                exchange_tz = ZoneInfo("Asia/Taipei")
            timestamps = result[0].get("timestamp",[])
            closes = result[0].get("indicators",{}).get("quote",[{}])[0].get("close",[])
            if not timestamps or not closes: continue
            result_dict = {}
            for ts, price in zip(timestamps, closes):
                if price is not None and float(price) > 0:
                    d = dt.datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(exchange_tz)
                    result_dict[format_short_date(d)] = round(float(price), 2)
            if result_dict:
                return result_dict
        except Exception as e:
            print(f"    Warning: failed to fetch {code}{suffix}: {e}")
            continue
    return {}

def fetch_all_prices(stocks):
    """??????????????"""
    results = {}
    def fetch_one(s):
        return s["code"], fetch_closing_prices(s["code"])

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_one, s): s for s in stocks}
        for future in as_completed(futures):
            code, price_dict = future.result()
            results[code] = price_dict
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

def get_github_raw_file(repo, filename, token):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{filename}",
        headers={"Authorization":f"token {token}","Accept":"application/vnd.github.raw+json","User-Agent":"stock-tracker"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")

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
        if file_info.get("content"):
            content = base64.b64decode(file_info["content"]).decode("utf-8")
        else:
            content = get_github_raw_file(GITHUB_REPO, STOCKS_FILE, github_token)
        sha = file_info["sha"]
        data = json.loads(content)
        stocks = data.get("stocks", [])
    except Exception as e:
        print(f"❌ 無法連接 GitHub：{e}"); input("\n按 Enter 關閉..."); return

    print(f"\n🔍 搜尋截圖資料夾：{SCREENSHOT_DIR}")
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)

    ai_rows_by_code = {}
    new_codes = set()
    screenshot_dt = None
    screenshot_short = None
    screenshot_full = None

    latest = get_latest_screenshot()
    if latest:
        try:
            screenshot_dt = get_screenshot_date(latest)
        except ValueError as e:
            print(f"Error: {e}")
            print("   Rename the screenshot to include a date, for example 2026-06-18 or 6-18.")
            input("\nPress Enter to close...")
            return

        screenshot_short = format_short_date(screenshot_dt)
        screenshot_full = format_full_date(screenshot_dt)
        if screenshot_dt.weekday() >= 5:
            print(f"Error: {screenshot_full} is a weekend, so no update was uploaded.")
            input("\nPress Enter to close...")
            return
        print(f"Found screenshot: {os.path.basename(latest)} (date: {screenshot_full})\nReading with AI...")
        try:
            result = read_screenshot_with_claude(latest, anthropic_key)
            if result and result.get("stocks"):
                added = 0
                updated = 0
                for row in result["stocks"]:
                    code = str(row.get("code", "")).strip()
                    if not code:
                        continue
                    row["code"] = code
                    ai_rows_by_code[code] = row
                    existing = next((x for x in stocks if x["code"] == code), None)
                    bandwidth = row.get("bandwidth")

                    if not existing:
                        new_stock = {
                            "code": code, "name": row.get("name", ""),
                            "sector": row.get("sector", ""), "side": "bull",
                            "prices": {}, "ma_total": None,
                            "bband_upper": None, "bband_lower": None,
                            "addedDate": screenshot_full
                        }
                        stocks.append(new_stock)
                        new_codes.add(code)
                        added += 1
                    else:
                        if bandwidth is not None and existing.get("ma_total") is not None:
                            ma_price = existing["ma_total"] / 20
                            existing["bband_upper"] = round(ma_price * (1 + float(bandwidth) / 200), 2)
                            existing["bband_lower"] = round(ma_price * (1 - float(bandwidth) / 200), 2)
                            updated += 1
                print(f"AI read {len(result['stocks'])} stocks; added {added}; updated bands {updated}.")
            else:
                print("Warning: could not read stocks from screenshot.")
        except Exception as e:
            print(f"Warning: AI read failed: {e}")
    else:
        print("Error: no dated screenshot was found; no update was uploaded.")
        input("\nPress Enter to close...")
        return

    today_str = screenshot_short
    removed_future = 0
    affected_stocks = 0
    for stock in stocks:
        removed = remove_prices_after(stock, screenshot_dt)
        if removed:
            removed_future += removed
            affected_stocks += 1
    if removed_future:
        print(f"\nRemoved {removed_future} prices later than {today_str} from {affected_stocks} stocks.")

    print(f"\nUpdating close prices ({today_str})...\nTracked stocks: {len(stocks)}")
    t0 = time.time()
    price_results = fetch_all_prices(stocks)
    target_hits = sum(1 for result in price_results.values() if today_str in result)
    minimum_hits = max(1, len(stocks) // 2)
    if target_hits < minimum_hits:
        print(f"\nError: Yahoo returned {today_str} data for only {target_hits}/{len(stocks)} stocks.")
        print("The target date may not be a trading day, or Yahoo may be unavailable. Nothing was uploaded.")
        input("\nPress Enter to close...")
        return
    print(f"Yahoo target-date coverage: {target_hits}/{len(stocks)}")

    success = 0
    for stock in stocks:
        code = stock.get("code", "")
        price_dict = price_results.get(code, {})
        ai_row = ai_rows_by_code.get(code)
        added_date = stock.get("addedDate")
        latest_existing_date = latest_price_date(stock)
        wrote_price = False

        # Never accept a Yahoo date later than the dated screenshot or a weekend.
        price_dict = {
            date_key: price for date_key, price in price_dict.items()
            if parse_date_key(date_key) <= screenshot_dt and parse_date_key(date_key).weekday() < 5
        }

        if screenshot_short and ai_row and screenshot_short not in price_dict and valid_price(ai_row.get("price")):
            use_fallback = True
            if added_date:
                try:
                    use_fallback = parse_date_key(screenshot_short) >= parse_date_key(added_date)
                except Exception:
                    use_fallback = True
            if latest_existing_date and parse_date_key(screenshot_short) < latest_existing_date:
                use_fallback = False
            if use_fallback:
                ai_price = ai_row.get("price")
                if stock.get("ma_total") is None:
                    init_ma_and_bands(stock, ai_price, ai_row.get("ma_deviation"), ai_row.get("bandwidth"))
                roll_ma = code not in new_codes
                if add_price(stock, screenshot_short, ai_price, roll_ma=roll_ma):
                    wrote_price = True
                    print(f"  Warning: {code} has no Yahoo price for {screenshot_short}; using screenshot price {ai_price}")

        if price_dict:
            if "prices" not in stock:
                stock["prices"] = {}
            for date_key, yahoo_price in sorted(price_dict.items(), key=lambda x: parse_date_key(x[0])):
                if added_date:
                    try:
                        if parse_date_key(date_key) < parse_date_key(added_date):
                            continue
                    except Exception:
                        pass
                if latest_existing_date and parse_date_key(date_key) < latest_existing_date:
                    continue

                is_screenshot_date = screenshot_short is not None and date_key == screenshot_short
                if is_screenshot_date and ai_row and valid_price(ai_row.get("price")):
                    ai_price = float(ai_row.get("price"))
                    if abs(float(yahoo_price) - ai_price) >= 0.01:
                        print(f"  Warning: {code} AI price {ai_price} differs from Yahoo {yahoo_price}; using Yahoo")

                if is_screenshot_date and ai_row and stock.get("ma_total") is None:
                    init_ma_and_bands(stock, yahoo_price, ai_row.get("ma_deviation"), ai_row.get("bandwidth"))

                added_on_target = False
                try:
                    added_on_target = added_date is not None and parse_date_key(added_date) == screenshot_dt
                except Exception:
                    pass

                if is_screenshot_date and added_on_target:
                    # A stock first added on this date was initialized from the
                    # screenshot's MA deviation, so do not roll the same day twice.
                    if ai_row:
                        init_ma_and_bands(stock, yahoo_price, ai_row.get("ma_deviation"), ai_row.get("bandwidth"))
                    if replace_price(stock, date_key, yahoo_price, adjust_ma=False):
                        wrote_price = True
                elif is_screenshot_date:
                    if replace_price(stock, date_key, yahoo_price, adjust_ma=True):
                        wrote_price = True
                else:
                    if add_price(stock, date_key, yahoo_price, roll_ma=True):
                        wrote_price = True

        if ai_row and ai_row.get("bandwidth") is not None and stock.get("ma_total") is not None:
            ma_price = stock["ma_total"] / 20
            stock["bband_upper"] = round(ma_price * (1 + float(ai_row.get("bandwidth")) / 200), 2)
            stock["bband_lower"] = round(ma_price * (1 - float(ai_row.get("bandwidth")) / 200), 2)

        target_price = stock.get("prices", {}).get(today_str)
        if valid_price(target_price):
            print(f"  OK {code} {stock.get('name','')} {target_price} ({today_str}) MA:{stock.get('ma','N/A')}")
            success += 1
        else:
            print(f"  Warning: {code} {stock.get('name','')} has no price for {today_str}")
    print(f"\nClose price update finished in {time.time()-t0:.1f}s, success {success}/{len(stocks)}")


    print("\nSkipping chart generation (disabled).")

    data["stocks"] = stocks
    data["lastUpdate"] = today_str
    new_content = json.dumps(data, ensure_ascii=False, indent=2)
    print(f"\n📤 上傳到 GitHub...")
    try:
        update_github_file(GITHUB_REPO, STOCKS_FILE, new_content, sha, github_token, f"更新 {today_str}")
        local_stocks_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), STOCKS_FILE)
        with open(local_stocks_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"✅ 完成！")
        print(f"Local data synchronized: {local_stocks_path}")
        print(f"\n🌐 儀表板：https://charles10roger01.github.io/stock-tracker/")
    except Exception as e: print(f"❌ 上傳失敗：{e}")

    input("\n按 Enter 關閉...")

if __name__ == "__main__":
    main()
