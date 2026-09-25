#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
🚀 Bybit Spot AI Screener Web Server (خادم واجهة الويب لفاحص الصفقات الفورية)
High-performance Threading HTTP Server with Built-in Technical Analysis Engine
==============================================================================
"""

import os
import sys
import json
import time
import hmac
import hashlib
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN

# Ensure UTF-8 or devnull output on Windows terminal / pyw.exe
if sys.platform.startswith("win"):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    else:
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    import requests
except ImportError:
    print("Error: 'requests' package is missing. Install with: pip install requests")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("Error: 'numpy' package is missing. Install with: pip install numpy")
    sys.exit(1)

PORT = int(os.environ.get("PORT", 5600))
HOST = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
BYBIT_BASE_URL = "https://api.bybit.com"

EXCLUDED_SYMBOLS = {
    "USDCUSDT", "USDEUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", 
    "EURUSDT", "GBPUSDT", "DAIUSDT", "CUSDUSDT", "USDDUSDT", "PYUSDUSDT",
    "WBTCUSDT", "WETHUSDT", "STETHUSDT", "USD1USDT", "RLUSDUSDT", "USDSUSDT",
    "USDYUSDT", "USDPUSDT", "USDXUSDT", "USTCUSDT", "MNTUSDT"
}

# Cache
CACHE_DATA = None
CACHE_TIME = 0

def is_stablecoin(symbol, price, high_24h, low_24h):
    if symbol in EXCLUDED_SYMBOLS:
        return True
    base = symbol.replace("USDT", "")
    if base.startswith("USD") or base.endswith("USD"):
        return True
    if 0.985 <= price <= 1.015 and (high_24h - low_24h) / price < 0.02:
        return True
    return False

def fetch_top_spot_symbols(min_turnover=700_000, max_pairs=80):
    url = f"{BYBIT_BASE_URL}/v5/market/tickers?category=spot"
    try:
        resp = requests.get(url, timeout=7)
        if resp.status_code != 200:
            return []
        data = resp.json()
        ticker_list = data.get("result", {}).get("list", [])
        
        usdt_pairs = []
        for t in ticker_list:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT") or symbol in EXCLUDED_SYMBOLS:
                continue
            
            try:
                turnover = float(t.get("turnover24h", 0))
                last_price = float(t.get("lastPrice", 0))
                price_chg = float(t.get("price24hPcnt", 0)) * 100
                high_24h = float(t.get("highPrice24h", 0))
                low_24h = float(t.get("lowPrice24h", 0))
            except (ValueError, TypeError):
                continue
            
            if turnover < min_turnover or last_price <= 0:
                continue

            if is_stablecoin(symbol, last_price, high_24h, low_24h):
                continue
            
            usdt_pairs.append({
                "symbol": symbol,
                "base_coin": symbol.replace("USDT", ""),
                "price": last_price,
                "turnover24h": turnover,
                "price_chg_24h": price_chg,
                "high24h": high_24h,
                "low24h": low_24h
            })
            
        usdt_pairs.sort(key=lambda x: x["turnover24h"], reverse=True)
        return usdt_pairs[:max_pairs]
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return []

def fetch_klines(symbol, interval="240", limit=100):
    url = f"{BYBIT_BASE_URL}/v5/market/kline?category=spot&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        raw_list = data.get("result", {}).get("list", [])
        if not raw_list or len(raw_list) < 35:
            return None
        return raw_list[::-1]
    except Exception:
        return None

def calculate_rsi(closes, period=14):
    if len(closes) < period + 2:
        return np.full_like(closes, 50.0)
    deltas = np.diff(closes)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(closes)
    rsi[:period] = 100. - 100. / (1. + rs)
    
    for i in range(period, len(closes)):
        delta = deltas[i - 1]
        upval = delta if delta > 0 else 0.0
        downval = -delta if delta < 0 else 0.0
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)
    return rsi

def calculate_ema(data, span):
    alpha = 2.0 / (span + 1.0)
    ema = np.empty_like(data)
    ema[0] = data[0]
    for i in range(1, len(data)):
        ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
    return ema

def calculate_macd(closes, fast=12, slow=26, signal=9):
    ema_fast = calculate_ema(closes, fast)
    ema_slow = calculate_ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def analyze_coin(coin_meta, interval="240"):
    symbol = coin_meta["symbol"]
    klines = fetch_klines(symbol, interval=interval, limit=100)
    if not klines or len(klines) < 40:
        return None

    try:
        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        opens = np.array([float(k[1]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])
    except (ValueError, TypeError):
        return None

    current_price = closes[-1]
    if current_price <= 0:
        return None

    rsi = calculate_rsi(closes, period=14)
    curr_rsi = rsi[-1]
    prev_rsi = rsi[-2]

    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, min(len(closes), 200)) if len(closes) >= 70 else ema50

    macd_line, signal_line, hist = calculate_macd(closes)

    avg_vol_20 = np.mean(volumes[-21:-1]) if len(volumes) >= 21 else np.mean(volumes)
    curr_vol = volumes[-1]
    vol_ratio = (curr_vol / avg_vol_20) if avg_vol_20 > 0 else 1.0

    recent_low_20 = np.min(lows[-20:])
    recent_high_20 = np.max(highs[-20:])
    prev_high_30 = np.max(highs[-30:-1]) if len(highs) >= 30 else recent_high_20

    candle_body = abs(closes[-1] - opens[-1])
    is_green = closes[-1] >= opens[-1]
    lower_wick = (min(opens[-1], closes[-1]) - lows[-1])
    has_rejection_wick = lower_wick > (candle_body * 0.8)

    setup_name = ""
    setup_type = ""
    score = 0
    reasons = []

    # 1. SETUP: Oversold Bounce
    if curr_rsi <= 36 or (prev_rsi <= 32 and curr_rsi > prev_rsi):
        score = 65
        setup_type = "OVERSOLD_BOUNCE"
        setup_name = "💎 اقتناص ارتداد قاع (Oversold Dip)"
        reasons.append(f"مؤشر RSI وصل لمنطقة تشبع بيعي حاد ({curr_rsi:.1f})")
        
        if is_green:
            score += 12
            reasons.append("ظهور شمعة خضراء ارتدادية تؤكد توقف البيع")
        if has_rejection_wick:
            score += 10
            reasons.append("ذيل سفلي شرائي طويل (رفض هبوطي قوي)")
        if vol_ratio > 1.2:
            score += 8
            reasons.append(f"تزايد حجم السيولة الارتدادية بنسبة {vol_ratio:.1f}x")
            
        stop_loss = min(current_price * 0.955, recent_low_20 * 0.985)
        tp1 = current_price * 1.045
        tp2 = current_price * 1.095
        tp3 = max(current_price * 1.18, recent_high_20)

    # 2. SETUP: Volume Breakout
    elif vol_ratio >= 1.7 and curr_rsi >= 50 and curr_rsi <= 72 and is_green:
        score = 70
        setup_type = "VOLUME_BREAKOUT"
        setup_name = "🚀 اختراق وزخم سيولة (Volume Breakout)"
        reasons.append(f"انفجار سيولة شرائية بمقدار {vol_ratio:.1f}x أضعاف المتوسط")
        
        if current_price >= prev_high_30 * 0.985:
            score += 15
            reasons.append("اختراق قمة المقاومة لآخر 30 شمعة")
        if macd_line[-1] > signal_line[-1] and hist[-1] > hist[-2]:
            score += 10
            reasons.append("تقاطع إيجابي متصاعد في مؤشر MACD")
        if current_price > ema20[-1] > ema50[-1]:
            score += 5
            reasons.append("ترتيب متصاعد للمتوسطات المتحركة EMA 20/50")

        stop_loss = max(current_price * 0.95, opens[-1] * 0.985)
        tp1 = current_price * 1.05
        tp2 = current_price * 1.11
        tp3 = current_price * 1.22

    # 3. SETUP: Golden Pullback
    elif current_price > ema50[-1] and (abs(current_price - ema50[-1]) / current_price < 0.025 or abs(current_price - ema20[-1]) / current_price < 0.015) and 40 <= curr_rsi <= 55:
        score = 68
        setup_type = "TREND_PULLBACK"
        setup_name = "📈 إعادة اختبار ترند صاعد (Golden Pullback)"
        reasons.append("ارتداد السعر بدقة من متوسط الدعم EMA 20/50")
        
        if curr_rsi > prev_rsi:
            score += 12
            reasons.append(f"انعطاف مؤشر RSI للأعلى من منطقة الأمان ({curr_rsi:.1f})")
        if is_green:
            score += 10
            reasons.append("ثبات شمعة خضراء أعلى خط الدعم المتحرك")
        if current_price > ema200[-1]:
            score += 8
            reasons.append("الترند العام صاعد بثبات أعلى EMA 200")

        stop_loss = min(current_price * 0.96, ema50[-1] * 0.98)
        tp1 = current_price * 1.04
        tp2 = current_price * 1.085
        tp3 = current_price * 1.16

    # 4. SETUP: Bullish Divergence
    else:
        min_p_idx = np.argmin(lows[-16:-1])
        if lows[-1] <= lows[-16 + min_p_idx] * 1.01 and rsi[-1] > rsi[-16 + min_p_idx] + 3.0 and curr_rsi < 48:
            score = 72
            setup_type = "BULLISH_DIVERGENCE"
            setup_name = "🔄 انفراج إيجابي للزخم (Bullish Divergence)"
            reasons.append(f"قاع سعر هابط يقابله قاع صاعد في مؤشر RSI ({curr_rsi:.1f})")
            if is_green:
                score += 12
                reasons.append("تأكيد انعكاس بشمعة إغلاق خضراء")
            
            stop_loss = min(current_price * 0.955, lows[-1] * 0.985)
            tp1 = current_price * 1.05
            tp2 = current_price * 1.10
            tp3 = current_price * 1.20

    if score < 72:
        return None

    sl_dist = current_price - stop_loss
    tp2_dist = tp2 - current_price
    rr_ratio = round(tp2_dist / sl_dist, 2) if sl_dist > 0 else 2.0

    if rr_ratio < 1.8:
        return None

    entry_min = round(min(current_price * 0.995, opens[-1]), 6)
    entry_max = round(current_price * 1.003, 6)

    def fmt(val):
        if val >= 100:
            return f"{val:.2f}"
        elif val >= 1:
            return f"{val:.4f}"
        else:
            return f"{val:.6f}"

    return {
        "symbol": symbol,
        "base_coin": coin_meta["base_coin"],
        "price": current_price,
        "price_formatted": fmt(current_price),
        "turnover24h_m": round(coin_meta["turnover24h"] / 1_000_000, 2),
        "price_chg_24h": round(coin_meta["price_chg_24h"], 2),
        "setup_type": setup_type,
        "setup_name": setup_name,
        "score": min(score, 98),
        "rsi": round(curr_rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "entry_min": entry_min,
        "entry_max": entry_max,
        "entry_display": f"{fmt(entry_min)} - {fmt(entry_max)}",
        "tp1": round(tp1, 6),
        "tp1_pct": round(((tp1 - current_price) / current_price) * 100, 1),
        "tp1_display": fmt(tp1),
        "tp2": round(tp2, 6),
        "tp2_pct": round(((tp2 - current_price) / current_price) * 100, 1),
        "tp2_display": fmt(tp2),
        "tp3": round(tp3, 6),
        "tp3_pct": round(((tp3 - current_price) / current_price) * 100, 1),
        "tp3_display": fmt(tp3),
        "sl": round(stop_loss, 6),
        "sl_pct": round(((current_price - stop_loss) / current_price) * 100, 1),
        "sl_display": fmt(stop_loss),
        "rr_ratio": rr_ratio,
        "reasons": reasons,
        "rationale_arabic": " | ".join(reasons),
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def run_screener_scan(interval="240", max_pairs=70):
    global CACHE_DATA, CACHE_TIME
    start_time = time.time()
    pairs = fetch_top_spot_symbols(min_turnover=700_000, max_pairs=max_pairs)
    if not pairs:
        return {"scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "setups": [], "error": "No pairs fetched"}

    results = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_symbol = {executor.submit(analyze_coin, p, interval): p["symbol"] for p in pairs}
        for future in as_completed(future_to_symbol):
            try:
                res = future.result()
                if res:
                    results.append(res)
            except Exception:
                pass

    results.sort(key=lambda x: (x["score"], x["turnover24h_m"]), reverse=True)
    elapsed = time.time() - start_time

    payload = {
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "interval": interval,
        "scanned_count": len(pairs),
        "recommended_count": len(results),
        "scan_duration_sec": round(elapsed, 2),
        "setups": results
    }

    CACHE_DATA = payload
    CACHE_TIME = time.time()

    # Save to disk
    json_path = os.path.join(WEB_DIR, "screener_latest.json")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return payload

GLOBAL_SPOT_TICKERS = {}
GLOBAL_SPOT_TICKERS_RAW = {}
GLOBAL_SPOT_TICKERS_TIME = 0

def get_all_spot_tickers_cached(ttl=2.0):
    global GLOBAL_SPOT_TICKERS, GLOBAL_SPOT_TICKERS_RAW, GLOBAL_SPOT_TICKERS_TIME
    now = time.time()
    if now - GLOBAL_SPOT_TICKERS_TIME < ttl and GLOBAL_SPOT_TICKERS:
        return GLOBAL_SPOT_TICKERS
    
    url = f"{BYBIT_BASE_URL}/v5/market/tickers?category=spot"
    try:
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            lst = r.json().get("result", {}).get("list", [])
            tickers = {}
            raw_map = {}
            for item in lst:
                sym = item.get("symbol")
                lp = item.get("lastPrice")
                if sym and lp:
                    try:
                        tickers[sym] = float(lp)
                        raw_map[sym] = item
                    except ValueError:
                        pass
            if tickers:
                GLOBAL_SPOT_TICKERS = tickers
                GLOBAL_SPOT_TICKERS_RAW = raw_map
                GLOBAL_SPOT_TICKERS_TIME = now
                return tickers
    except Exception as e:
        print(f"Error fetching spot tickers: {e}")
    
    return GLOBAL_SPOT_TICKERS

def get_market_overview():
    """Fetch BTC, ETH, SOL for the top pulse banner."""
    get_all_spot_tickers_cached(ttl=2.0)
    m = {}
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        item = GLOBAL_SPOT_TICKERS_RAW.get(sym)
        if item:
            m[sym] = {
                "price": float(item.get("lastPrice", 0)),
                "chg": float(item.get("price24hPcnt", 0)) * 100
            }
    return m

def load_bybit_config():
    # 1. Check environment variables (for Cloud / Koyeb / Render)
    env_key = os.environ.get("BYBIT_API_KEY")
    env_sec = os.environ.get("BYBIT_API_SECRET")
    if env_key and env_sec:
        return {"apiKey": env_key.strip(), "apiSecret": env_sec.strip()}

    # 2. Check local config files
    paths = [
        os.path.join(WEB_DIR, "config.json"),
        os.path.join(os.path.dirname(WEB_DIR), "bybit_spot_trader", "config.json"),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if cfg.get("apiKey") and cfg.get("apiSecret"):
                        return cfg
            except Exception:
                pass

    # 3. Built-in Cloud Production Fallback (Unified Trading Account)
    return {
        "apiKey": "10TSpdK1sHGhhPXkKs",
        "apiSecret": "OXpQhZQPGY19EBl3s9QFISiKbPZZio5UVe6m"
    }

def save_bybit_config(api_key, api_secret):
    cfg = {
        "apiKey": api_key.strip(),
        "apiSecret": api_secret.strip(),
        "savedAt": datetime.now().isoformat()
    }
    for p in [os.path.join(WEB_DIR, "config.json"), os.path.join(os.path.dirname(WEB_DIR), "bybit_spot_trader", "config.json")]:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
        except Exception:
            pass
    return cfg

# ==============================================================================
# 🎮 Paper Trading / Demo Account Persistence & Helpers
# ==============================================================================
DEMO_FILE = os.path.join(WEB_DIR, "demo_account.json")

def load_demo_account():
    if os.path.exists(DEMO_FILE):
        try:
            with open(DEMO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "balanceUsdt" in data and "openPositions" in data:
                    return data
        except Exception:
            pass
    # Default initial demo account: $10,000.00 USDT
    default_acc = {
        "balanceUsdt": 10000.0,
        "initialBalance": 10000.0,
        "openPositions": [],
        "history": [],
        "updatedAt": datetime.now().isoformat()
    }
    save_demo_account(default_acc)
    return default_acc

def save_demo_account(data):
    data["updatedAt"] = datetime.now().isoformat()
    try:
        with open(DEMO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving demo account: {e}")

def get_demo_account_summary():
    acc = load_demo_account()
    open_pos = acc.get("openPositions", [])
    history = acc.get("history", [])
    
    changed = False
    
    if open_pos:
        tickers = get_all_spot_tickers_cached(ttl=2.0)
        still_open = []
        for pos in open_pos:
            sym = pos.get("symbol")
            live_price = tickers.get(sym)
            if live_price and live_price > 0:
                pos["currentPrice"] = live_price
                qty = float(pos.get("qty", 0))
                gross = qty * live_price
                fee = gross * 0.001
                pos["currentValue"] = round(gross - fee, 2)
                pos["unrealizedPnL"] = round(pos["currentValue"] - float(pos.get("amountUsdt", 0)), 2)
                entry_price = float(pos.get("entryPrice", live_price))
                if entry_price > 0:
                    pos["unrealizedPnLPct"] = round(((live_price - entry_price) / entry_price) * 100, 2)
                
                # Multi-Target Take Profit & Stop Loss Logic
                tp1 = float(pos.get("tp1", 0) or 0)
                tp2 = float(pos.get("tp2", 0) or 0)
                tp_strat = pos.get("tpStrategy", "TP1_TP2")
                sl_target = float(pos.get("sl", 0) or 0)

                # Check SL hit first
                if sl_target > 0 and live_price <= sl_target:
                    realized_pnl = round(pos["currentValue"] - float(pos.get("amountUsdt", 0)), 2)
                    net_return = round(pos["currentValue"], 2)
                    acc["balanceUsdt"] = round(acc.get("balanceUsdt", 0) + net_return, 2)
                    hist_entry = {
                        "id": pos.get("id"),
                        "symbol": sym,
                        "baseCoin": pos.get("baseCoin"),
                        "entryPrice": entry_price,
                        "closePrice": live_price,
                        "qty": qty,
                        "amountUsdt": float(pos.get("amountUsdt", 0)),
                        "netReturn": net_return,
                        "realizedPnL": realized_pnl,
                        "pnlPct": pos["unrealizedPnLPct"],
                        "exitFee": round(fee, 4),
                        "exitReason": f"تفعيل وقف الخسارة الإلزامي (${sl_target}) 🛡️",
                        "setupName": pos.get("setupName", "صفقة محاكاة"),
                        "closedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    history.insert(0, hist_entry)
                    changed = True
                    continue

                # Check Full TP2 Hit (or final target reached)
                if tp2 > 0 and live_price >= tp2:
                    realized_pnl = round(pos["currentValue"] - float(pos.get("amountUsdt", 0)), 2)
                    net_return = round(pos["currentValue"], 2)
                    acc["balanceUsdt"] = round(acc.get("balanceUsdt", 0) + net_return, 2)
                    hist_entry = {
                        "id": pos.get("id"),
                        "symbol": sym,
                        "baseCoin": pos.get("baseCoin"),
                        "entryPrice": entry_price,
                        "closePrice": live_price,
                        "qty": qty,
                        "amountUsdt": float(pos.get("amountUsdt", 0)),
                        "netReturn": net_return,
                        "realizedPnL": realized_pnl,
                        "pnlPct": pos["unrealizedPnLPct"],
                        "exitFee": round(fee, 4),
                        "exitReason": f"تحقيق الهدف الثاني الرئيسي (TP2: ${tp2}) وتسييل الصفقة 🎯🚀",
                        "setupName": pos.get("setupName", "صفقة محاكاة"),
                        "closedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    history.insert(0, hist_entry)
                    changed = True
                    continue

                # Check TP1 Hit:
                if tp1 > 0 and live_price >= tp1:
                    if tp_strat == "TP1":
                        # Full close at TP1
                        realized_pnl = round(pos["currentValue"] - float(pos.get("amountUsdt", 0)), 2)
                        net_return = round(pos["currentValue"], 2)
                        acc["balanceUsdt"] = round(acc.get("balanceUsdt", 0) + net_return, 2)
                        hist_entry = {
                            "id": pos.get("id"),
                            "symbol": sym,
                            "baseCoin": pos.get("baseCoin"),
                            "entryPrice": entry_price,
                            "closePrice": live_price,
                            "qty": qty,
                            "amountUsdt": float(pos.get("amountUsdt", 0)),
                            "netReturn": net_return,
                            "realizedPnL": realized_pnl,
                            "pnlPct": pos["unrealizedPnLPct"],
                            "exitFee": round(fee, 4),
                            "exitReason": f"تحقيق الهدف الأول (TP1: ${tp1}) وإغلاق الصفقة بالكامل 🎯",
                            "setupName": pos.get("setupName", "صفقة محاكاة"),
                            "closedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        history.insert(0, hist_entry)
                        changed = True
                        continue

                    elif tp_strat == "TP1_TP2" and not pos.get("tp1Executed"):
                        # Partial Close: Sell 50% of the position, lock in profit, move SL to Entry (Breakeven)
                        half_qty = round(qty * 0.5, 4)
                        half_cost = round(float(pos.get("amountUsdt", 0)) * 0.5, 2)
                        half_gross = half_qty * live_price
                        half_fee = round(half_gross * 0.001, 4)
                        half_net = round(half_gross - half_fee, 2)
                        half_pnl = round(half_net - half_cost, 2)
                        half_pct = round(((live_price - entry_price) / entry_price) * 100, 2)

                        # Return 50% + profit to wallet
                        acc["balanceUsdt"] = round(acc.get("balanceUsdt", 0) + half_net, 2)

                        # Record in history
                        hist_entry = {
                            "id": f"{pos.get('id')}-TP1",
                            "symbol": sym,
                            "baseCoin": pos.get("baseCoin"),
                            "entryPrice": entry_price,
                            "closePrice": live_price,
                            "qty": half_qty,
                            "amountUsdt": half_cost,
                            "netReturn": half_net,
                            "realizedPnL": half_pnl,
                            "pnlPct": half_pct,
                            "exitFee": half_fee,
                            "exitReason": f"تحقيق هدف 1 (TP1: ${tp1}) | بيع 50% وتأمين الدخول (Breakeven) 🎯",
                            "setupName": pos.get("setupName", "صفقة محاكاة"),
                            "closedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        history.insert(0, hist_entry)

                        # Update remaining 50% of position
                        pos["qty"] = round(qty - half_qty, 4)
                        pos["amountUsdt"] = round(float(pos.get("amountUsdt", 0)) - half_cost, 2)
                        rem_gross = pos["qty"] * live_price
                        rem_fee = rem_gross * 0.001
                        pos["currentValue"] = round(rem_gross - rem_fee, 2)
                        pos["unrealizedPnL"] = round(pos["currentValue"] - pos["amountUsdt"], 2)
                        pos["tp1Executed"] = True
                        pos["tp1LockedPnL"] = half_pnl
                        # Move SL to Entry Price for Breakeven!
                        pos["sl"] = entry_price
                        changed = True

                changed = True
            
            still_open.append(pos)
            
        acc["openPositions"] = still_open
        acc["history"] = history
        if changed:
            save_demo_account(acc)
        open_pos = still_open

    total_pos_value = sum(p.get("currentValue", p.get("amountUsdt", 0)) for p in open_pos)
    total_equity = round(acc.get("balanceUsdt", 0) + total_pos_value, 2)
    
    total_trades = len(history)
    winning_trades = sum(1 for h in history if h.get("realizedPnL", 0) > 0)
    losing_trades = sum(1 for h in history if h.get("realizedPnL", 0) < 0)
    win_rate = round((winning_trades / total_trades * 100), 1) if total_trades > 0 else 0.0
    total_realized_pnl = round(sum(h.get("realizedPnL", 0) for h in history), 2)
    
    return {
        "retCode": 0,
        "balanceUsdt": round(acc.get("balanceUsdt", 0), 2),
        "initialBalance": acc.get("initialBalance", 10000.0),
        "totalEquity": total_equity,
        "openPositions": open_pos,
        "history": history,
        "stats": {
            "totalTrades": total_trades,
            "winningTrades": winning_trades,
            "losingTrades": losing_trades,
            "winRate": win_rate,
            "totalRealizedPnL": total_realized_pnl
        }
    }

def get_bybit_signature(api_secret, timestamp, api_key, recv_window, payload):
    param_str = f"{timestamp}{api_key}{recv_window}{payload}"
    return hmac.new(
        api_secret.encode("utf-8"),
        param_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

# ==============================================================================
# 🔴 Live Real Positions Persistence & Helpers
# ==============================================================================
REAL_POS_FILE = os.path.join(WEB_DIR, "real_positions.json")

def load_real_positions():
    if os.path.exists(REAL_POS_FILE):
        try:
            with open(REAL_POS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "openPositions" in data:
                    return data
        except Exception:
            pass
    default_acc = {
        "openPositions": [],
        "history": [],
        "updatedAt": datetime.now().isoformat()
    }
    save_real_positions(default_acc)
    return default_acc

def save_real_positions(data):
    data["updatedAt"] = datetime.now().isoformat()
    try:
        with open(REAL_POS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving real positions: {e}")

_LAST_BYBIT_SYNC = 0

def sync_real_positions_with_bybit():
    global _LAST_BYBIT_SYNC
    now = time.time()
    # Cache sync every 4 seconds to be gentle on Bybit rate limits
    if now - _LAST_BYBIT_SYNC < 4:
        return
    _LAST_BYBIT_SYNC = now
    
    cfg = load_bybit_config()
    if not cfg:
        return

    try:
        res = bybit_signed_request("GET", "/v5/execution/list", params={"category": "spot", "limit": "20"})
        if res.get("retCode") != 0:
            return
            
        exec_list = res.get("result", {}).get("list", [])
        if not exec_list:
            return

        acc = load_real_positions()
        open_pos = acc.get("openPositions", [])
        history = acc.get("history", [])
        changed = False

        recorded_exec_ids = set()
        for h in history:
            if h.get("execId"):
                recorded_exec_ids.add(h["execId"])
            if h.get("orderId"):
                recorded_exec_ids.add(h["orderId"])

        for e in exec_list:
            if e.get("side") != "Sell":
                continue
            exec_id = e.get("execId")
            order_id = e.get("orderId")
            if (exec_id and exec_id in recorded_exec_ids) or (order_id and order_id in recorded_exec_ids):
                continue

            sym = e.get("symbol")
            exec_price = float(e.get("execPrice", 0) or 0)
            exec_qty = float(e.get("execQty", 0) or 0)
            exec_val = float(e.get("execValue", 0) or 0)
            exec_time_ms = int(e.get("execTime", 0) or 0)
            closed_at = datetime.fromtimestamp(exec_time_ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if exec_time_ms else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            matched_pos = None
            for p in open_pos:
                if p.get("symbol") == sym:
                    matched_pos = p
                    break

            if matched_pos and exec_qty > 0 and exec_price > 0:
                entry_price = float(matched_pos.get("entryPrice", exec_price))
                cost = round(exec_qty * entry_price, 2)
                pnl = round(exec_val - cost, 2)
                pnl_pct = round(((exec_price - entry_price) / entry_price) * 100, 2) if entry_price > 0 else 0.0

                tp2_target = float(matched_pos.get("tp2", 0) or 0)
                is_tp2 = tp2_target > 0 and abs(exec_price - tp2_target) / tp2_target < 0.02
                exit_reason = f"تحقيق الهدف الثاني (TP2: ${exec_price}) وتسييل الصفقة 🎯🚀" if is_tp2 else f"تحقيق الهدف الأول (TP1: ${exec_price}) | بيع 50% وتأمين الدخول 🎯"

                hist_entry = {
                    "id": f"{matched_pos.get('id')}-{order_id or int(now)}",
                    "execId": exec_id,
                    "orderId": order_id,
                    "symbol": sym,
                    "baseCoin": matched_pos.get("baseCoin", sym.replace("USDT", "")),
                    "entryPrice": entry_price,
                    "closePrice": exec_price,
                    "qty": exec_qty,
                    "amountUsdt": cost,
                    "netReturn": round(exec_val, 2),
                    "realizedPnL": pnl,
                    "pnlPct": pnl_pct,
                    "exitReason": exit_reason,
                    "closedAt": closed_at
                }
                history.insert(0, hist_entry)
                recorded_exec_ids.add(exec_id or order_id)

                curr_qty = float(matched_pos.get("qty", 0) or 0)
                remaining_qty = max(0.0, curr_qty - exec_qty)
                if remaining_qty <= 0.001 or is_tp2:
                    open_pos.remove(matched_pos)
                else:
                    matched_pos["qty"] = round(remaining_qty, 4)
                    matched_pos["amountUsdt"] = round(max(0.0, float(matched_pos.get("amountUsdt", 0)) - cost), 2)
                    matched_pos["tp1Executed"] = True
                    matched_pos["tp1LockedPnL"] = pnl
                    matched_pos["sl"] = entry_price
                    matched_pos["setupName"] = f"صفقة حقيقية Bybit (تحقق الهدف الأول 🎯)"
                changed = True

        if changed:
            acc["openPositions"] = open_pos
            acc["history"] = history
            save_real_positions(acc)
    except Exception as ex:
        print(f"⚠️ Error syncing Bybit real executions: {ex}")

def get_real_positions_summary():
    sync_real_positions_with_bybit()
    acc = load_real_positions()
    open_pos = acc.get("openPositions", [])
    history = acc.get("history", [])

    
    if open_pos:
        tickers = get_all_spot_tickers_cached(ttl=2.0)
        for pos in open_pos:
            sym = pos.get("symbol")
            live_price = tickers.get(sym)
            if live_price and live_price > 0:
                pos["currentPrice"] = live_price
                qty = float(pos.get("qty", 0))
                gross = qty * live_price
                pos["currentValue"] = round(gross, 2)
                amount = float(pos.get("amountUsdt", 0))
                pos["unrealizedPnL"] = round(pos["currentValue"] - amount, 2)
                entry_price = float(pos.get("entryPrice", live_price))
                if entry_price > 0:
                    pos["unrealizedPnLPct"] = round(((live_price - entry_price) / entry_price) * 100, 2)
    
    total_pos_value = sum(p.get("currentValue", p.get("amountUsdt", 0)) for p in open_pos)
    total_invested = sum(float(p.get("amountUsdt", 0)) for p in open_pos)
    total_unrealized_pnl = round(sum(p.get("unrealizedPnL", 0) for p in open_pos), 2)
    
    total_trades = len(history)
    winning_trades = sum(1 for h in history if float(h.get("realizedPnL", 0)) > 0)
    losing_trades = sum(1 for h in history if float(h.get("realizedPnL", 0)) < 0)
    win_rate = round((winning_trades / total_trades * 100), 1) if total_trades > 0 else 0.0
    total_realized_pnl = round(sum(float(h.get("realizedPnL", 0)) for h in history), 2)
    
    return {
        "retCode": 0,
        "openPositions": open_pos,
        "history": history,
        "totalInvested": total_invested,
        "totalValue": total_pos_value,
        "totalUnrealizedPnL": total_unrealized_pnl,
        "stats": {
            "totalTrades": total_trades,
            "winningTrades": winning_trades,
            "losingTrades": losing_trades,
            "winRate": win_rate,
            "totalRealizedPnL": total_realized_pnl
        }
    }


# ==============================================================================
# 🎯 Bybit Instruments Precision & Lot Size Cache
# ==============================================================================
_INSTRUMENTS_CACHE = {}
_INSTRUMENTS_CACHE_TIME = 0

def get_instrument_info(symbol: str) -> dict:
    global _INSTRUMENTS_CACHE, _INSTRUMENTS_CACHE_TIME
    now = time.time()
    if not _INSTRUMENTS_CACHE or (now - _INSTRUMENTS_CACHE_TIME > 14400):
        try:
            url = f"{BYBIT_BASE_URL}/v5/market/instruments-info?category=spot"
            r = requests.get(url, timeout=10).json()
            if r.get("retCode") == 0:
                _INSTRUMENTS_CACHE = {item["symbol"]: item for item in r.get("result", {}).get("list", [])}
                _INSTRUMENTS_CACHE_TIME = now
        except Exception as e:
            print(f"⚠️ Error fetching instruments-info: {e}")
    return _INSTRUMENTS_CACHE.get(symbol, {})

def round_to_step(value, step_str, rounding=ROUND_HALF_UP) -> str:
    if value is None or not step_str:
        return str(value) if value is not None else ""
    try:
        d_val = Decimal(str(value))
        d_step = Decimal(str(step_str))
        if d_step <= 0:
            return str(value)
        units = (d_val / d_step).quantize(Decimal("1"), rounding=rounding)
        res = units * d_step
        if "." in step_str:
            dec_places = len(step_str.split(".")[1])
            return f"{res:.{dec_places}f}"
        return str(int(res))
    except Exception:
        return str(value)

def bybit_signed_request(method, endpoint, params=None, json_body=None):
    cfg = load_bybit_config()
    if not cfg:
        return {"retCode": -1, "retMsg": "مفاتيح Bybit API غير مهيأة. يرجى ضبطها من نافذة الإعدادات."}
    
    api_key = cfg["apiKey"]
    api_secret = cfg["apiSecret"]
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    
    payload_str = ""
    qs = ""
    if method == "GET":
        if params:
            qs = urllib.parse.urlencode(sorted(params.items()))
            payload_str = qs
    elif method == "POST":
        if json_body:
            payload_str = json.dumps(json_body, separators=(",", ":"))
            
    sig = get_bybit_signature(api_secret, ts, api_key, recv_window, payload_str)
    
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sig,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN-TYPE": "2",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    base_urls = ["https://api.bybit.com", "https://api.bytick.com"]
    last_err = ""
    
    for base in base_urls:
        url = f"{base}{endpoint}"
        if method == "GET" and qs:
            url = f"{url}?{qs}"
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=8)
            else:
                resp = requests.post(url, headers=headers, data=payload_str, timeout=8)
            
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 403:
                last_err = "منصة Bybit تحظر خوادم أمريكا (Oregon). الحل: اختر منطقة فرانكفورت (ألمانيا) أو سنغافورة في Render."
            else:
                last_err = f"كود استجابة غير متوقع من بايبت: {resp.status_code}"
        except Exception as e:
            last_err = str(e)
            
    return {"retCode": -1, "retMsg": f"خطأ في الاتصال بخوادم بايبت: {last_err}"}


class ScreenerWebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/scan":
            qs = urllib.parse.parse_qs(parsed.query)
            interval = qs.get("interval", ["240"])[0]
            limit = int(qs.get("limit", ["70"])[0])
            data = run_screener_scan(interval=interval, max_pairs=limit)
            self._send_json(data)
            return

        elif path == "/api/latest":
            global CACHE_DATA
            if CACHE_DATA is None:
                json_path = os.path.join(WEB_DIR, "screener_latest.json")
                alt_json_path = os.path.join(os.path.dirname(WEB_DIR), "bybit_spot_trader", "screener_latest.json")
                for p in [json_path, alt_json_path]:
                    if os.path.exists(p):
                        try:
                            with open(p, "r", encoding="utf-8") as f:
                                CACHE_DATA = json.load(f)
                                break
                        except Exception:
                            pass
            if CACHE_DATA is None:
                CACHE_DATA = {"setups": [], "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            self._send_json(CACHE_DATA)
            return

        elif path == "/api/pulse":
            pulse = get_market_overview()
            self._send_json(pulse)
            return

        elif path == "/api/account/status":
            cfg = load_bybit_config()
            has_keys = bool(cfg and cfg.get("apiKey") and cfg.get("apiSecret") and len(cfg.get("apiKey")) > 5)
            masked = ""
            if has_keys:
                k = cfg["apiKey"]
                masked = k[:4] + "..." + k[-4:] if len(k) > 8 else "****"
            self._send_json({"connected": has_keys, "maskedKey": masked, "accountType": "UNIFIED"})
            return

        elif path == "/api/account/balance":
            res = bybit_signed_request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
            if res.get("retCode") == 0:
                acc = res.get("result", {}).get("list", [{}])[0]
                total_equity = float(acc.get("totalEquity", 0) or 0)
                total_wallet = float(acc.get("totalWalletBalance", 0) or 0)
                coins = acc.get("coin", [])
                usdt_available = 0.0
                holdings = []
                for c in coins:
                    bal = float(c.get("walletBalance", 0) or 0)
                    avail = float(c.get("availableToWithdraw", 0) or 0)
                    usd_val = float(c.get("usdValue", 0) or 0)
                    if c.get("coin") == "USDT":
                        usdt_available = avail if avail > 0 else bal
                    if bal > 0:
                        holdings.append({
                            "coin": c.get("coin"),
                            "balance": bal,
                            "available": avail,
                            "usdValue": usd_val
                        })
                holdings.sort(key=lambda x: x["usdValue"], reverse=True)
                self._send_json({
                    "retCode": 0,
                    "connected": True,
                    "totalEquity": total_equity,
                    "totalWallet": total_wallet,
                    "usdtAvailable": usdt_available,
                    "holdings": holdings
                })
            else:
                self._send_json(res)
            return

        elif path == "/api/orders/open":
            qs = urllib.parse.parse_qs(parsed.query)
            sym = qs.get("symbol", [None])[0]
            params = {"category": "spot"}
            if sym:
                params["symbol"] = sym
            res = bybit_signed_request("GET", "/v5/order/realtime", params=params)
            self._send_json(res)
            return

        elif path == "/api/demo/state":
            summary = get_demo_account_summary()
            self._send_json(summary)
            return

        elif path == "/api/real/positions":
            summary = get_real_positions_summary()
            self._send_json(summary)
            return

        elif path == "/api/kline":
            qs = urllib.parse.parse_qs(parsed.query)
            sym = qs.get("symbol", ["BTCUSDT"])[0].upper()
            interval = qs.get("interval", ["240"])[0]
            limit = int(qs.get("limit", ["150"])[0])
            url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={sym}&interval={interval}&limit={limit}"
            try:
                r = requests.get(url, timeout=6)
                self._send_json(r.json())
            except Exception as e:
                self._send_json({"retCode": -1, "retMsg": str(e)})
            return

        # Serve static files
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            req_json = json.loads(post_data.decode("utf-8"))
        except Exception:
            req_json = {}

        if path == "/api/save-keys":
            api_key = req_json.get("apiKey", "")
            api_secret = req_json.get("apiSecret", "")
            if not api_key or not api_secret:
                self._send_json({"success": False, "error": "يرجى إدخال مفتاح الـ API والـ Secret"})
                return
            save_bybit_config(api_key, api_secret)
            self._send_json({"success": True, "message": "تم حفظ المفاتيح بنجاح وتفعيل حساب Bybit V5!"})
            return

        elif path == "/api/order/place":
            symbol = req_json.get("symbol", "").upper()
            side = req_json.get("side", "Buy").capitalize()
            order_type = req_json.get("orderType", "Market").capitalize()
            qty = str(req_json.get("qty", ""))
            price = str(req_json.get("price", "")) if req_json.get("price") else None
            
            # Fetch symbol precision filters from Bybit
            info = get_instrument_info(symbol)
            price_filter = info.get("priceFilter", {})
            lot_filter = info.get("lotSizeFilter", {})
            tick_size = price_filter.get("tickSize", "0.0001")
            base_prec = lot_filter.get("basePrecision", "0.01")

            body = {
                "category": "spot",
                "symbol": symbol,
                "side": side,
                "orderType": order_type
            }

            # Format Quantity
            if order_type == "Market" and side == "Buy":
                market_unit = req_json.get("marketUnit", "quoteCoin")
                if market_unit == "quoteCoin":
                    body["marketUnit"] = "quoteCoin"
                    try:
                        body["qty"] = f"{float(qty):.2f}"
                    except Exception:
                        body["qty"] = str(qty)
                else:
                    body["qty"] = round_to_step(qty, base_prec, rounding=ROUND_DOWN)
            else:
                body["qty"] = round_to_step(qty, base_prec, rounding=ROUND_DOWN)

            # Format Limit Price & Limit TP/SL
            tp = req_json.get("takeProfit")
            sl = req_json.get("stopLoss")

            if order_type == "Limit":
                if price:
                    body["price"] = round_to_step(price, tick_size)
                body["timeInForce"] = "GTC"
                if tp:
                    body["takeProfit"] = round_to_step(tp, tick_size)
                    body["tpOrderType"] = "Market"
                    body["tpTriggerBy"] = "LastPrice"
                if sl:
                    body["stopLoss"] = round_to_step(sl, tick_size)
                    body["slOrderType"] = "Market"
                    body["slTriggerBy"] = "LastPrice"

            # Execute Primary Order
            res = bybit_signed_request("POST", "/v5/order/create", json_body=body)

            # If Market Buy succeeded and TP was requested, place Take Profit Limit Sell for the filled coins
            if res.get("retCode") == 0 and side == "Buy":
                order_id = res.get("result", {}).get("orderId")
                try:
                    time.sleep(0.7)
                    hist = bybit_signed_request("GET", "/v5/order/history", params={"category": "spot", "orderId": order_id})
                    orders = hist.get("result", {}).get("list", [])
                    exec_qty = 0
                    exec_price = float(price) if price else 0
                    if orders:
                        exec_qty = float(orders[0].get("cumExecQty", 0) or 0)
                        exec_price = float(orders[0].get("avgPrice", 0) or exec_price)
                    if not exec_qty:
                        exec_qty = float(qty) if order_type == "Limit" else 0
                        
                    tp_order_id = None
                    if order_type == "Market" and tp and exec_qty > 0:
                        # In spot market buys, Bybit deducts spot fee (0.1%) from base coin.
                        # Query wallet balance or subtract 0.1% to get true available quantity to sell:
                        sell_qty = exec_qty * 0.999
                        base_coin = symbol.replace("USDT", "")
                        try:
                            wb = bybit_signed_request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED", "coin": base_coin})
                            c_list = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
                            if c_list:
                                w_bal = float(c_list[0].get("walletBalance", 0) or 0)
                                if w_bal > 0:
                                    sell_qty = min(exec_qty, w_bal)
                        except Exception:
                            pass

                        tp_strategy = req_json.get("tpStrategy", "TP1_TP2")
                        tp1_target = req_json.get("tp1")
                        tp2_target = req_json.get("tp2")

                        # If TP1_TP2 strategy is chosen and both targets exist, split 50% / 50%
                        if tp_strategy == "TP1_TP2" and tp1_target and tp2_target and float(tp1_target) > 0 and float(tp2_target) > 0:
                            half1 = float(round_to_step(sell_qty / 2.0, base_prec, rounding=ROUND_DOWN))
                            half2 = float(round_to_step(sell_qty - half1, base_prec, rounding=ROUND_DOWN))

                            order_ids = []
                            for h_qty, h_tp in [(half1, tp1_target), (half2, tp2_target)]:
                                if h_qty > 0:
                                    s_body = {
                                        "category": "spot",
                                        "symbol": symbol,
                                        "side": "Sell",
                                        "orderType": "Limit",
                                        "qty": round_to_step(h_qty, base_prec, rounding=ROUND_DOWN),
                                        "price": round_to_step(h_tp, tick_size),
                                        "timeInForce": "GTC"
                                    }
                                    h_res = bybit_signed_request("POST", "/v5/order/create", json_body=s_body)
                                    if h_res.get("retCode") == 0:
                                        order_ids.append(h_res.get("result", {}).get("orderId"))
                            if order_ids:
                                tp_order_id = ", ".join(order_ids)
                                res["tpOrderId"] = tp_order_id
                                res["tpMessage"] = f"تم تفعيل هدفي الخروج (50% عند ${tp1_target} و 50% عند ${tp2_target}) مباشرة في دفتر طلبات Bybit!"
                        else:
                            # Single TP order
                            sell_body = {
                                "category": "spot",
                                "symbol": symbol,
                                "side": "Sell",
                                "orderType": "Limit",
                                "qty": round_to_step(sell_qty, base_prec, rounding=ROUND_DOWN),
                                "price": round_to_step(tp, tick_size),
                                "timeInForce": "GTC"
                            }

                            tp_res = bybit_signed_request("POST", "/v5/order/create", json_body=sell_body)
                            if tp_res.get("retCode") == 0:
                                tp_order_id = tp_res.get("result", {}).get("orderId")
                                res["tpOrderId"] = tp_order_id
                                res["tpMessage"] = f"تم تفعيل أمر جني الأرباح (Limit Sell عند ${sell_body['price']}) مباشرة على Bybit!"
                            else:
                                print(f"⚠️ Initial TP/SL order returned code {tp_res.get('retCode')}: {tp_res.get('retMsg')} - retrying with exact wallet balance...")
                                try:
                                    wb2 = bybit_signed_request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED", "coin": base_coin})
                                    c_list2 = wb2.get("result", {}).get("list", [{}])[0].get("coin", [])
                                    if c_list2:
                                        w_bal2 = float(c_list2[0].get("walletBalance", 0) or 0)
                                        if w_bal2 > 0:
                                            sell_body["qty"] = round_to_step(w_bal2, base_prec, rounding=ROUND_DOWN)
                                            tp_res2 = bybit_signed_request("POST", "/v5/order/create", json_body=sell_body)
                                            if tp_res2.get("retCode") == 0:
                                                tp_order_id = tp_res2.get("result", {}).get("orderId")
                                                res["tpOrderId"] = tp_order_id
                                                sl_info = f" ووقف الخسارة عند ${sell_body['triggerPrice']}" if sl else ""
                                                res["tpMessage"] = f"تم تفعيل أمر جني الأرباح (TP: ${sell_body['price']}){sl_info} رسمياً على Bybit!"
                                except Exception as fb_err:
                                    print(f"⚠️ Fallback TP/SL order error: {fb_err}")

                    # Save to real_positions.json
                    real_acc = load_real_positions()
                    new_real_pos = {
                        "id": f"REAL-{int(time.time())}-{symbol[:4]}",
                        "symbol": symbol,
                        "baseCoin": symbol.replace("USDT", ""),
                        "side": "Buy",
                        "orderType": order_type,
                        "entryPrice": exec_price or float(orders[0].get("basePrice", 0) if orders else 0),
                        "currentPrice": exec_price,
                        "qty": exec_qty,
                        "amountUsdt": float(qty) if (order_type == "Market" and req_json.get("marketUnit") == "quoteCoin") else round(exec_qty * exec_price, 2),
                        "feePaid": round(exec_qty * exec_price * 0.001, 3),
                        "currentValue": round(exec_qty * exec_price, 2),
                        "unrealizedPnL": 0.0,
                        "unrealizedPnLPct": 0.0,
                        "tp1": float(req_json.get("tp1", 0)) if req_json.get("tp1") else (float(tp) if tp else None),
                        "tp2": float(req_json.get("tp2", 0)) if req_json.get("tp2") else (float(tp) if tp else None),
                        "tp3": float(req_json.get("tp3", 0)) if req_json.get("tp3") else None,
                        "sl": float(sl) if sl else None,
                        "setupName": req_json.get("setupName", "صفقة حقيقية Bybit"),
                        "openedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "OPEN",
                        "orderId": order_id,
                        "tpOrderId": tp_order_id
                    }
                    real_acc["openPositions"].insert(0, new_real_pos)
                    save_real_positions(real_acc)
                except Exception as e:
                    print(f"⚠️ Error recording real position: {e}")

            self._send_json(res)
            return

        elif path == "/api/real/close":
            pos_id = req_json.get("positionId", "")
            real_acc = load_real_positions()
            pos = next((p for p in real_acc.get("openPositions", []) if p["id"] == pos_id), None)
            if not pos:
                self._send_json({"retCode": -1, "retMsg": "لم يتم العثور على الصفقة المطلوبة"})
                return
            sym = pos["symbol"]
            qty = pos.get("qty", 0)
            tp_order_id = pos.get("tpOrderId")
            # 1. Cancel open TP order if any
            if tp_order_id:
                try:
                    bybit_signed_request("POST", "/v5/order/cancel", json_body={"category": "spot", "symbol": sym, "orderId": tp_order_id})
                except Exception:
                    pass
            # 2. Place market sell order
            info = get_instrument_info(sym)
            base_prec = info.get("lotSizeFilter", {}).get("basePrecision", "0.01")
            sell_body = {
                "category": "spot",
                "symbol": sym,
                "side": "Sell",
                "orderType": "Market",
                "qty": round_to_step(qty, base_prec, rounding=ROUND_DOWN)
            }
            sell_res = bybit_signed_request("POST", "/v5/order/create", json_body=sell_body)
            if sell_res.get("retCode") == 0:
                real_acc["openPositions"] = [p for p in real_acc["openPositions"] if p["id"] != pos_id]
                pos["status"] = "CLOSED"
                pos["closedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                real_acc.get("history", []).insert(0, pos)
                save_real_positions(real_acc)
            self._send_json(sell_res)
            return

        elif path == "/api/order/cancel":
            symbol = req_json.get("symbol", "").upper()
            order_id = req_json.get("orderId", "")
            body = {
                "category": "spot",
                "symbol": symbol,
                "orderId": order_id
            }
            res = bybit_signed_request("POST", "/v5/order/cancel", json_body=body)
            self._send_json(res)
            return

        # ==============================================================================
        # 🎮 Paper Trading / Demo API Endpoints
        # ==============================================================================
        elif path == "/api/demo/order":
            symbol = req_json.get("symbol", "").upper()
            side = req_json.get("side", "Buy").capitalize()
            order_type = req_json.get("orderType", "Market").capitalize()
            amount_usdt = float(req_json.get("amountUsdt", 0) or 0)
            price = float(req_json.get("price", 0) or 0)
            setup_name = req_json.get("setupName", "صفقة محاكاة")
            tp1 = float(req_json.get("tp1", 0)) if req_json.get("tp1") else None
            tp2 = float(req_json.get("tp2", 0)) if req_json.get("tp2") else None
            tp3 = float(req_json.get("tp3", 0)) if req_json.get("tp3") else None
            sl = float(req_json.get("sl", 0)) if req_json.get("sl") else None
            tp_strategy = req_json.get("tpStrategy", "TP1_TP2")
            score = req_json.get("score", 0)

            if amount_usdt <= 0:
                self._send_json({"retCode": -1, "retMsg": "المبلغ يجب أن يكون أكبر من 0 USDT"})
                return

            if price <= 0:
                self._send_json({"retCode": -1, "retMsg": "سعر الدخول غير صالح"})
                return

            acc = load_demo_account()
            current_bal = float(acc.get("balanceUsdt", 0))
            if current_bal < amount_usdt:
                self._send_json({
                    "retCode": -1, 
                    "retMsg": f"رصيد الـ USDT التجريبي غير كافٍ. المتاح: ${current_bal:.2f} USDT، المطلوب: ${amount_usdt:.2f} USDT"
                })
                return

            fee = round(amount_usdt * 0.001, 4) # 0.10% Spot Fee
            coins_qty = round((amount_usdt - fee) / price, 6)
            pos_id = f"DEMO-{int(time.time())}-{symbol[:4]}"

            position = {
                "id": pos_id,
                "symbol": symbol,
                "baseCoin": symbol.replace("USDT", ""),
                "side": side,
                "orderType": order_type,
                "entryPrice": price,
                "currentPrice": price,
                "qty": coins_qty,
                "amountUsdt": amount_usdt,
                "feePaid": fee,
                "currentValue": amount_usdt,
                "unrealizedPnL": 0.0,
                "unrealizedPnLPct": 0.0,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "sl": sl,
                "tpStrategy": tp_strategy,
                "setupName": setup_name,
                "score": score,
                "openedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "OPEN"
            }

            acc["balanceUsdt"] = round(current_bal - amount_usdt, 2)
            acc.setdefault("openPositions", []).insert(0, position)
            save_demo_account(acc)

            self._send_json({
                "retCode": 0,
                "retMsg": "تم تنفيذ الصفقة التجريبية بنجاح في المحفظة الافتراضية!",
                "result": {
                    "orderId": pos_id,
                    "position": position,
                    "remainingBalance": acc["balanceUsdt"]
                }
            })
            return

        elif path == "/api/demo/close":
            pos_id = req_json.get("positionId")
            close_price = float(req_json.get("closePrice", 0) or 0)
            reason = req_json.get("reason", "إغلاق يدوي وتسييل أرباح")

            acc = load_demo_account()
            open_pos = acc.get("openPositions", [])
            target_idx = next((i for i, p in enumerate(open_pos) if p["id"] == pos_id), -1)

            if target_idx == -1:
                self._send_json({"retCode": -1, "retMsg": "لم يتم العثور على الصفقة في المحفظة الافتراضية"})
                return

            pos = open_pos.pop(target_idx)
            if close_price <= 0:
                tickers = get_all_spot_tickers_cached(ttl=2.0)
                close_price = tickers.get(pos["symbol"], pos.get("currentPrice", pos["entryPrice"]))

            gross_return = pos["qty"] * close_price
            exit_fee = round(gross_return * 0.001, 4)
            net_return = round(gross_return - exit_fee, 2)
            realized_pnl = round(net_return - pos["amountUsdt"], 2)
            pnl_pct = round(((close_price - pos["entryPrice"]) / pos["entryPrice"]) * 100, 2) if pos["entryPrice"] > 0 else 0.0

            acc["balanceUsdt"] = round(acc.get("balanceUsdt", 0) + net_return, 2)

            closed_record = {
                "id": pos["id"],
                "symbol": pos["symbol"],
                "baseCoin": pos["baseCoin"],
                "entryPrice": pos["entryPrice"],
                "closePrice": close_price,
                "qty": pos["qty"],
                "amountUsdt": pos["amountUsdt"],
                "netReturn": net_return,
                "realizedPnL": realized_pnl,
                "pnlPct": pnl_pct,
                "exitFee": exit_fee,
                "exitReason": reason,
                "setupName": pos.get("setupName", "صفقة محاكاة"),
                "openedAt": pos["openedAt"],
                "closedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            acc.setdefault("history", []).insert(0, closed_record)
            save_demo_account(acc)

            self._send_json({
                "retCode": 0,
                "retMsg": f"تم إغلاق الصفقة بنجاح | الربح/الخسارة: {realized_pnl:+.2f} USDT ({pnl_pct:+.2f}%)",
                "result": closed_record,
                "newBalance": acc["balanceUsdt"]
            })
            return

        elif path == "/api/demo/reset":
            new_amount = float(req_json.get("amount", 10000.0) or 10000.0)
            clear_history = bool(req_json.get("clearHistory", False))

            acc = load_demo_account()
            acc["balanceUsdt"] = new_amount
            acc["initialBalance"] = new_amount
            acc["openPositions"] = []
            if clear_history:
                acc["history"] = []
            save_demo_account(acc)

            self._send_json({
                "retCode": 0,
                "retMsg": f"تمت إعادة تعيين المحفظة التجريبية بنجاح إلى ${new_amount:,.2f} USDT!",
                "balanceUsdt": new_amount
            })
            return

        self._send_json({"error": "Unknown POST route"}, status=404)

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, format, *args):
        try:
            sys.stdout.write(f"[{datetime.now().strftime('%H:%M:%S')}] %s\n" % (format % args))
            sys.stdout.flush()
        except Exception:
            pass

    def _send_json(self, data, status=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            pass

def is_port_responding(port=PORT):
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            resp.read()
            return resp.status == 200
    except Exception:
        pass
    return False

def kill_port_owner(port=PORT):
    import subprocess
    try:
        res = subprocess.run(f"netstat -ano | findstr :{port}", shell=True, capture_output=True, text=True)
        pids = set()
        for line in res.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and "LISTENING" in parts[3]:
                try:
                    pid = int(parts[4])
                    if pid > 4 and pid != os.getpid():
                        pids.add(pid)
                except ValueError:
                    pass
        for pid in pids:
            subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
        if pids:
            time.sleep(0.4)
    except Exception:
        pass

def background_stop_loss_guard():
    print("🛡️ [SL & TP Guard] Autonomous 24/7 background trade monitor active.")
    while True:
        try:
            time.sleep(2.0)
            # 1. Automatically sync filled TP limit orders directly from Bybit's live trades
            sync_real_positions_with_bybit()

            acc = load_real_positions()
            open_pos = acc.get("openPositions", [])
            if not open_pos:
                continue
            tickers = get_all_spot_tickers_cached(ttl=2.0)
            changed = False
            for pos in list(open_pos):
                sym = pos.get("symbol")
                sl = float(pos.get("sl") or 0)
                live_price = tickers.get(sym)
                if live_price and live_price > 0 and sl > 0 and live_price <= sl:
                    print(f"\n🚨 [SL GUARD TRIGGERED] {sym}: live ${live_price} <= SL ${sl}!")
                    # 1. Cancel open TP limit orders on Bybit for this symbol
                    tp_ids = str(pos.get("tpOrderId") or "").split(",")
                    for tpid in tp_ids:
                        tpid = tpid.strip()
                        if tpid:
                            try:
                                bybit_signed_request("POST", "/v5/order/cancel", json_body={"category": "spot", "symbol": sym, "orderId": tpid})
                            except Exception:
                                pass
                    time.sleep(0.4)
                    # 2. Query available wallet balance and sell at Market
                    base_coin = pos.get("baseCoin", sym.replace("USDT", ""))
                    try:
                        wb = bybit_signed_request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED", "coin": base_coin})
                        c_list = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
                        avail_qty = float(c_list[0].get("walletBalance", 0) or 0) if c_list else 0
                        if avail_qty > 0:
                            info = get_instrument_info(sym)
                            base_prec = info.get("lotSizeFilter", {}).get("basePrecision", "0.01")
                            sell_q = round_to_step(avail_qty, base_prec, rounding=ROUND_DOWN)
                            m_res = bybit_signed_request("POST", "/v5/order/create", json_body={
                                "category": "spot",
                                "symbol": sym,
                                "side": "Sell",
                                "orderType": "Market",
                                "qty": str(sell_q)
                            })
                            print(f"🚨 [SL Guard] Market Sell for {sym} result: {m_res}")
                    except Exception as sl_err:
                        print(f"⚠️ [SL Guard] Error selling {sym}: {sl_err}")

                    # 3. Move position to history
                    open_pos.remove(pos)
                    pos["status"] = "CLOSED"
                    pos["closeReason"] = "STOP_LOSS_HIT"
                    pos["closePrice"] = live_price
                    pos["closedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    acc.setdefault("history", []).insert(0, pos)
                    changed = True
            if changed:
                save_real_positions(acc)
        except Exception as e:
            pass

def start_server():
    import webbrowser
    import threading

    # Start background Stop-Loss guard daemon
    threading.Thread(target=background_stop_loss_guard, daemon=True).start()

    # 1. If server is already running, open browser and exit cleanly
    if is_port_responding(PORT):
        print("\n" + "=" * 72)
        print("  ⚡ BYBIT SPOT AI SCREENER PRO")
        print("  ✅ الخادم نشط ويعمل بالفعل في خلفية النظام على المنفذ 5600!")
        print(f"  🌐 جاري فتح واجهة المنصة الآن في متصفحك: http://127.0.0.1:{PORT}/")
        print("=" * 72 + "\n")
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        except Exception:
            pass
        sys.exit(0)

    # 2. Clean up any stale/hung port occupant
    kill_port_owner(PORT)

    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True

    try:
        server = ThreadingHTTPServer((HOST, PORT), ScreenerWebHandler)
    except Exception as e:
        print("\n" + "=" * 72)
        print(f"  ❌ خطأ في ربط المنفذ {PORT}: {e}")
        print("=" * 72 + "\n")
        try:
            input("اضغط Enter للخروج...")
        except Exception:
            pass
        sys.exit(1)

    # 3. Schedule browser opening only if running locally on desktop
    is_cloud = bool(os.environ.get("RENDER") or os.environ.get("KOYEB_APP_NAME") or os.environ.get("PORT"))
    if not is_cloud:
        def launch_browser():
            time.sleep(0.7)
            try:
                webbrowser.open(f"http://127.0.0.1:{PORT}/")
            except Exception:
                pass
        threading.Thread(target=launch_browser, daemon=True).start()

    print("\n" + "=" * 72)
    print("  ⚡ BYBIT SPOT AI SCREENER PRO | فاحص صفقات بايبت الفورية الاحترافي ⚡")
    print("=" * 72)
    print(f"  🌐 رابط الواجهة المحلي : http://127.0.0.1:{PORT}/")
    print("  ✅ تم تشغيل الخادم بنجاح وجاري فتح الواجهة تلقائياً في متصفحك!")
    print("  💡 (اترك هذه النافذة مفتوحة طالما تستخدم المنصة | اضغط Ctrl+C للإيقاف)")
    print("=" * 72 + "\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] تم إيقاف الخادم بطلب من المستخدم...")
        server.server_close()
        sys.exit(0)
    except Exception as ex:
        print(f"\n[!] حدث خطأ غير متوقع: {ex}")
        server.server_close()
        sys.exit(1)

if __name__ == "__main__":
    start_server()

