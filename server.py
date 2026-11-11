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
import urllib.request
import threading
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

# ==============================================================================
# 📱 Telegram Alert System & Capital Protection Engine
# ==============================================================================
import bot_protection

TELEGRAM_BOT_TOKEN = bot_protection.get_secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = bot_protection.get_secret("TELEGRAM_CHAT_ID")

def send_telegram_alert(text: str, parse_mode: str = "HTML") -> bool:
    token = bot_protection.get_secret("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = bot_protection.get_secret("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"⚠️ [Telegram Alert Error] {e}")
        return False

def async_telegram_alert(text: str):
    threading.Thread(target=send_telegram_alert, args=(text,), daemon=True).start()

bot_protection.set_alert_function(async_telegram_alert)

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

def fetch_top_spot_symbols(min_turnover=1_500_000, max_pairs=80):
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

def get_market_regime():
    """
    Evaluates global market health using BTCUSDT on 4H & 24h trend.
    Protects against buying altcoins during market-wide dumps.
    """
    try:
        btc_klines = fetch_klines("BTCUSDT", interval="240", limit=60)
        if not btc_klines or len(btc_klines) < 30:
            return {
                "status": "NEUTRAL",
                "label": "السوق العام: متذبذب/حذر ⚠️",
                "is_safe": False,
                "btc_price": 0.0,
                "btc_rsi": 50.0,
                "btc_chg_24h": 0.0,
                "warning": "بيانات البيتكوين غير مكتملة، التداول بحذر."
            }

        all_closes = np.array([float(k[4]) for k in btc_klines])
        btc_price = all_closes[-1]
        # حساب المؤشرات على الشموع المغلقة فقط لمنع تغير قراءة السوق أثناء تكون الشمعة
        closed_closes = all_closes[:-1] if len(all_closes) > 51 else all_closes
        ema50 = calculate_ema(closed_closes, 50)[-1]
        rsi = calculate_rsi(closed_closes, period=14)[-1]

        # 24h change from tickers
        btc_item = GLOBAL_SPOT_TICKERS_RAW.get("BTCUSDT")
        chg_24h = float(btc_item.get("price24hPcnt", 0)) * 100 if btc_item else 0.0

        if btc_price > ema50 and rsi >= 48 and chg_24h >= -2.0:
            return {
                "status": "BULLISH",
                "label": "السوق العام صاعد وآمن (Bullish) 🟢",
                "is_safe": True,
                "btc_price": round(float(btc_price), 2),
                "btc_rsi": round(float(rsi), 1),
                "btc_chg_24h": round(float(chg_24h), 2),
                "warning": ""
            }
        elif btc_price < ema50 and (rsi < 45 or chg_24h < -2.5):
            return {
                "status": "BEARISH",
                "label": "السوق العام هابط / تصحيحي (Risk-Off) 🔴",
                "is_safe": False,
                "btc_price": round(float(btc_price), 2),
                "btc_rsi": round(float(rsi), 1),
                "btc_chg_24h": round(float(chg_24h), 2),
                "warning": "البيتكوين تحت ضغط بيعي وأسفل EMA 50. تم تشديد معايير الترشيح لمنع الارتدادات الوهمية وتفادي وقف الخسارة."
            }
        else:
            return {
                "status": "NEUTRAL",
                "label": "السوق العام متذبذب بحذر (Choppy/Neutral) 🟡",
                "is_safe": False,
                "btc_price": round(float(btc_price), 2),
                "btc_rsi": round(float(rsi), 1),
                "btc_chg_24h": round(float(chg_24h), 2),
                "warning": "حركة عرضية غير مستقرة للبيتكوين. يوصى بالتركيز فقط على صفقات النخبة (Score 85+)."
            }
    except Exception as e:
        return {
            "status": "NEUTRAL",
            "label": "حالة السوق: متذبذب 🟡",
            "is_safe": False,
            "btc_price": 0.0,
            "btc_rsi": 50.0,
            "btc_chg_24h": 0.0,
            "warning": str(e)
        }

def analyze_coin(coin_meta, interval="240", market_regime=None, enable_mtf=True):
    symbol = coin_meta["symbol"]
    klines = fetch_klines(symbol, interval=interval, limit=105)
    if not klines or len(klines) < 45:
        return None

    try:
        # الشمعة الأخيرة في klines هي شمعة حية غير مغلقة بعد؛ نأخذ منها السعر اللحظي فقط
        live_candle = klines[-1]
        live_price = float(live_candle[4])
        # جميع المؤشرات وإشارات الشموع تحسب على الشموع المغلقة المؤكدة فقط (Anti-Repainting)
        closed_klines = klines[:-1]
        closes = np.array([float(k[4]) for k in closed_klines])
        highs = np.array([float(k[2]) for k in closed_klines])
        lows = np.array([float(k[3]) for k in closed_klines])
        opens = np.array([float(k[1]) for k in closed_klines])
        volumes = np.array([float(k[5]) for k in closed_klines])
    except (ValueError, TypeError):
        return None

    signal_close = closes[-1]
    current_price = live_price if live_price > 0 else signal_close
    if current_price <= 0 or signal_close <= 0:
        return None

    # فلتر حماية من المطاردة أو الكسر اللحظي بعد إغلاق شمعة الإشارة:
    # 1) إذا هبط السعر اللحظي أسفل قاع شمعة الإشارة المغلقة فالإشارة لاغية
    # 2) إذا طار السعر اللحظي أكثر من 3.8% فوق إغلاق شمعة الإشارة فالدخول متأخر (منع الشراء في القمة)
    if current_price < lows[-1] * 0.996:
        return None
    if (current_price - signal_close) / signal_close > 0.038:
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
    has_rejection_wick = lower_wick > (candle_body * 0.9)

    is_market_bearish = market_regime and market_regime.get("status") == "BEARISH"

    setup_name = ""
    setup_type = ""
    score = 0
    reasons = []

    # =========================================================================
    # 🎯 1. SETUP: Oversold Institutional Bounce (أعلى نسبة نجاح تاريخية 60%+)
    # =========================================================================
    if (
        len(rsi) >= 4
        and np.min(rsi[-4:-1]) < 29.5
        and curr_rsi >= 31.0
        and curr_rsi > prev_rsi + 1.5
        and is_green
        and (vol_ratio >= 1.35 or has_rejection_wick)
        and hist[-1] > hist[-2]
        and signal_close >= ema50[-1] * 0.82
    ):
        score = 84
        setup_type = "OVERSOLD_BOUNCE"
        setup_name = "💎 اقتناص ارتداد قاع مؤسسي (Oversold Dip)"
        reasons.append(f"تشبع بيعي حاد مع ارتداد مؤكد في RSI ({curr_rsi:.1f}) وامتصاص مؤسسي")
        if vol_ratio >= 1.5:
            score += 6
            reasons.append(f"سيولة شرائية ارتدادية قوية {vol_ratio:.1f}x")
        if has_rejection_wick:
            score += 5
            reasons.append("ذيل شرائي سفلي طويل يرفض الهبوط")
        if signal_close > closes[-2]:
            score += 3
            reasons.append("إغلاق شمعة خضراء أعلى من الإغلاق السابق")
        recent_floor = float(np.min(lows[-7:]))
        stop_loss = min(current_price * 0.955, recent_floor * 0.985)

    # =========================================================================
    # 🎯 2. PRIORITY SETUP: Golden Pullback (إعادة اختبار ترند صاعد)
    # =========================================================================
    elif (
        signal_close > ema50[-1] >= ema200[-1] * 0.995
        and 34 <= prev_rsi <= 46
        and curr_rsi > prev_rsi + 2.0
        and is_green
        and signal_close > closes[-2]
        and hist[-1] > hist[-2]
        and (lows[-1] <= ema50[-1] * 1.018 or has_rejection_wick)
    ):
        score = 84
        setup_type = "TREND_PULLBACK"
        setup_name = "📈 إعادة اختبار ترند صاعد (Golden Pullback)"
        reasons.append("ارتداد صحي من دعم المتوسط المتحرك EMA 50 داخل مسار صاعد")
        if signal_close > ema200[-1] and ema50[-1] > ema200[-1]:
            score += 6
            reasons.append("الترند العام صاعد بثبات أعلى EMA 50 و EMA 200")
        if has_rejection_wick or vol_ratio >= 1.2:
            score += 5
            reasons.append("شمعة ارتداد إيجابية مع سيولة داعمة")
        swing_support = float(np.min(lows[-11:])) * 0.988
        stop_loss = min(current_price * 0.955, swing_support)

    # =========================================================================
    # 🎯 3. SETUP: Bullish Divergence (انفراج إيجابي مزدوج مع قاع سعري)
    # =========================================================================
    elif len(lows) >= 25:
        sub_lows = lows[-21:-3]
        sub_rsi = rsi[-21:-3]
        sub_hist = hist[-21:-3]
        if len(sub_lows) > 0:
            min_idx = int(np.argmin(sub_lows))
            prev_swing_low = float(sub_lows[min_idx])
            prev_swing_rsi = float(sub_rsi[min_idx])
            prev_swing_hist = float(sub_hist[min_idx])
            curr_low = float(np.min(lows[-3:]))

            has_div = (
                prev_swing_low * 0.992 <= curr_low <= prev_swing_low * 1.012
                and prev_swing_rsi <= 36.0
                and curr_rsi >= prev_swing_rsi + 3.5
                and curr_rsi > prev_rsi + 2.0
                and hist[-1] > prev_swing_hist
                and hist[-1] > hist[-2]
            )
            confirmed_reversal = (
                is_green
                and signal_close > max(closes[-2], opens[-2])
                and (has_rejection_wick or vol_ratio >= 1.25)
                and signal_close >= ema50[-1] * 0.90
            )
            if has_div and confirmed_reversal:
                score = 83
                setup_type = "BULLISH_DIVERGENCE"
                setup_name = "🔄 انفراج إيجابي للزخم (Bullish Divergence)"
                reasons.append(f"قاع مزدوج مع انفراج إيجابي مؤكد في RSI ({curr_rsi:.1f}) و MACD")
                if vol_ratio >= 1.35:
                    score += 5
                    reasons.append(f"تأكيد دخول سيولة شرائية بنسبة {vol_ratio:.1f}x")
                if has_rejection_wick:
                    score += 5
                    reasons.append("ذيل سفلي شرائي يؤكد رفض كسر القاع")
                stop_loss = min(current_price * 0.955, curr_low * 0.985)

    # =========================================================================
    # 🎯 4. SETUP: Volume Climax Reversal / Breakout (انفجار سيولة شرائية من دعم)
    # =========================================================================
    if score == 0:
        if (
            vol_ratio >= 1.50
            and 30 <= prev_rsi <= 39
            and curr_rsi > prev_rsi + 2.5
            and is_green
            and signal_close > closes[-2]
            and hist[-1] > hist[-2]
            and signal_close >= ema50[-1] * 0.86
        ):
            score = 82
            setup_type = "VOLUME_BREAKOUT"
            setup_name = "🚀 اختراق وزخم سيولة شرائية (Volume Breakout)"
            reasons.append(f"تدفق سيولة مؤسسية بمقدار {vol_ratio:.1f}x مع انعطاف سعري صاعد")
            if vol_ratio >= 2.0:
                score += 6
                reasons.append("انفجار فوليوم قياسي يتجاوز ضعفي المتوسط")
            if signal_close > opens[-2]:
                score += 4
                reasons.append("ابتلاع شرائي كامل للشمعة الهابطة السابقة")
            recent_floor = float(np.min(lows[-7:]))
            stop_loss = min(current_price * 0.955, recent_floor * 0.985)

    # =========================================================================
    # ⏱️ 5. Multi-Timeframe (MTF 1H) Confirmation Filter (تأكيد فريم الساعة)
    # =========================================================================
    if score >= 76 and enable_mtf and interval != "60":
        try:
            klines_1h = fetch_klines(symbol, interval="60", limit=55)
            if klines_1h and len(klines_1h) >= 35:
                closes_1h = np.array([float(k[4]) for k in klines_1h[:-1]])
                ema20_1h = calculate_ema(closes_1h, 20)[-1]
                ema50_1h = calculate_ema(closes_1h, min(len(closes_1h), 50))[-1]
                rsi_1h = calculate_rsi(closes_1h, period=14)[-1]
                if closes_1h[-1] >= ema20_1h and ema20_1h >= ema50_1h * 0.995 and 46 <= rsi_1h <= 72:
                    score += 5
                    reasons.append(f"تأكيد إيجابي متوافق على فريم الساعة 1H (RSI: {rsi_1h:.1f})")
                elif closes_1h[-1] < ema50_1h * 0.975 and rsi_1h < 38 and setup_type in ("TREND_PULLBACK", "VOLUME_BREAKOUT"):
                    score -= 8
                    reasons.append("⚠️ ضعف لحظي على فريم الساعة 1H — تم خفض التقييم للحماية")
        except Exception:
            pass

    # 🚫 Strict Qualification Filter: Threshold raised to 78!
    if score < 78:
        return None

    # Dynamic Clamp on Stop Loss (4.5% to 7.5% on 4H Spot):
    sl_dist = current_price - stop_loss
    sl_pct = (sl_dist / current_price) * 100

    if sl_pct < 4.5:
        stop_loss = current_price * 0.955
        sl_dist = current_price - stop_loss
    elif sl_pct > 7.5:
        return None

    tp1 = current_price + (sl_dist * 0.90)
    tp2 = current_price + (sl_dist * 2.15)
    tp3 = current_price + (sl_dist * 3.40)

    rr_ratio = round((tp2 - current_price) / sl_dist, 2)
    if rr_ratio < 1.85:
        return None

    is_elite = score >= 85
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
        "is_elite": is_elite,
        "quality_label": "💎 فرصة نخبة فائقة الدقة" if is_elite else "⭐ فرصة إيجابية مؤكدة",
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
    market_regime = get_market_regime()
    pairs = fetch_top_spot_symbols(min_turnover=1_500_000, max_pairs=max_pairs)
    if not pairs:
        return {
            "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "setups": [],
            "market_regime": market_regime,
            "error": "No pairs fetched"
        }

    results = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_symbol = {executor.submit(analyze_coin, p, interval, market_regime): p["symbol"] for p in pairs}
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
        "elite_count": sum(1 for r in results if r.get("is_elite")),
        "market_regime": market_regime,
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

    # 3. Built-in Cloud Production Fallback (Disabled)
    return {
        "apiKey": "",
        "apiSecret": ""
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
        prot_settings = bot_protection.load_settings()
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

                # تحديث الوقف المتحرك وتأمين الدخول (Breakeven + Fees / Lock TP1 / Trailing Stop)
                upd = bot_protection.compute_protective_stop(pos, live_price, prot_settings)
                pos["highestPrice"] = upd["highest"]
                pos["originalSl"] = upd["originalSl"]
                if upd["changed"]:
                    pos["sl"] = upd["sl"]
                    pos["protectionStage"] = upd["stage"]
                    changed = True
                
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
                    stage_now = pos.get("protectionStage", "INITIAL")
                    exit_lbl = (
                        f"تأمين ربح تلقائي ({stage_now} @ ${sl_target:,.4f}) 🛡️✅"
                        if realized_pnl >= 0
                        else f"تفعيل وقف الخسارة الإلزامي (${sl_target}) 🛡️"
                    )
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
                        "exitReason": exit_lbl,
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
                        # Move SL to Breakeven + Fee Buffer (+0.25%)!
                        be_price = bot_protection.breakeven_price(entry_price, prot_settings)
                        if not pos.get("originalSl"):
                            pos["originalSl"] = float(pos.get("sl") or 0)
                        pos["sl"] = max(float(pos.get("sl") or 0), be_price)
                        pos["protectionStage"] = "BREAKEVEN"
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
        "analytics": bot_protection.compute_journal_analytics(history),
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
REAL_POS_BAK_FILE = os.path.join(WEB_DIR, "real_positions_backup.json")
REAL_POS_SEED_FILE = os.path.join(WEB_DIR, "real_positions_seed.json")
REAL_POS_LOCK = threading.RLock()
_REAL_POS_CACHE = None

def heal_and_recalc_history(acc):
    """Auto-heals and recalculates historical trades that are missing realizedPnL, have corrupted amountUsdt/qty mismatches, or wrong exitReason."""
    if not isinstance(acc, dict):
        return False
    changed = False
    for h in acc.get("history", []):
        entry_p = float(h.get("entryPrice", 0) or 0)
        close_p = float(h.get("closePrice", 0) or 0)
        qty = float(h.get("qty", 0) or 0)
        raw_cost = float(h.get("amountUsdt", 0) or 0)
        true_cost = round(entry_p * qty, 2) if (entry_p > 0 and qty > 0) else raw_cost

        # إصلاح أي تعارض حسابي في الصفقات القديمة حيث كانت amountUsdt أكبر بكثير من (qty * entryPrice)
        cost_mismatch = (true_cost > 0 and abs(raw_cost - true_cost) / true_cost > 0.08)
        r_pnl = h.get("realizedPnL")
        needs_pnl_calc = (
            cost_mismatch
            or r_pnl is None
            or (float(r_pnl) == 0.0 and entry_p > 0 and close_p > 0 and abs(entry_p - close_p) / entry_p > 0.005)
        )

        if needs_pnl_calc and entry_p > 0 and close_p > 0 and qty > 0:
            net_ret = round(close_p * qty, 2)
            pnl = round(net_ret - true_cost, 2)
            pct = round(((close_p - entry_p) / entry_p) * 100, 2)
            h["amountUsdt"] = true_cost
            h["netReturn"] = net_ret
            h["realizedPnL"] = pnl
            h["pnlPct"] = pct
            changed = True

        # تصحيح سبب الخروج لو كان مكتوباً "تحقيق الهدف الأول" بينما الصفقة أُغلقت بخسارة
        curr_pnl = float(h.get("realizedPnL", 0) or 0)
        exit_r = str(h.get("exitReason") or "")
        if curr_pnl < -0.05 and ("تحقيق الهدف" in exit_r or not exit_r):
            h["exitReason"] = f"إغلاق / وقف خسارة (${close_p:,.4f}) 🛑"
            changed = True
        elif not exit_r:
            if h.get("closeReason") == "STOP_LOSS_HIT":
                h["exitReason"] = f"تفعيل وقف الخسارة (${close_p:,.4f}) 🛑"
            elif curr_pnl < -0.005:
                h["exitReason"] = f"إغلاق بخسارة (${close_p:,.4f}) 🛑"
            elif curr_pnl > 0.005:
                h["exitReason"] = f"تحقيق ربح (${close_p:,.4f}) 🎯"
            else:
                h["exitReason"] = f"إغلاق الصفقة (${close_p:,.4f})"
            changed = True
    return changed

def load_real_positions():
    global _REAL_POS_CACHE
    with REAL_POS_LOCK:
        # 1. Check primary file first
        if os.path.exists(REAL_POS_FILE) and os.path.getsize(REAL_POS_FILE) > 20:
            try:
                with open(REAL_POS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "openPositions" in data:
                    try:
                        if heal_and_recalc_history(data):
                            save_real_positions(data)
                    except Exception:
                        pass
                    _REAL_POS_CACHE = data
                    return data
            except Exception as err:
                print(f"⚠️ [Real Positions] Read error on {os.path.basename(REAL_POS_FILE)}: {err}")

        # 2. Check backup and seed file if primary is missing or invalid
        candidates = [REAL_POS_BAK_FILE, REAL_POS_SEED_FILE]
        for fpath in candidates:
            if os.path.exists(fpath) and os.path.getsize(fpath) > 20:
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "openPositions" in data:
                        _REAL_POS_CACHE = data
                        return data
                except Exception as err:
                    print(f"⚠️ [Real Positions] Read error on {os.path.basename(fpath)}: {err}")

        # 3. Return memory cache if present
        if _REAL_POS_CACHE and isinstance(_REAL_POS_CACHE, dict):
            return _REAL_POS_CACHE

        default_acc = {
            "openPositions": [],
            "history": [],
            "ignoredExecIds": [],
            "stats": {"totalTrades": 0, "winningTrades": 0, "losingTrades": 0, "winRate": 0.0, "totalRealizedPnL": 0.0},
            "updatedAt": datetime.now().isoformat()
        }
        _REAL_POS_CACHE = default_acc
        return default_acc

def save_real_positions(data, force=False):
    global _REAL_POS_CACHE
    if not isinstance(data, dict):
        return
    with REAL_POS_LOCK:
        # Anti-Wipeout Guard: Never overwrite valid positions/history with empty arrays unless explicitly forced
        curr_open = len((_REAL_POS_CACHE or {}).get("openPositions", []))
        curr_hist = len((_REAL_POS_CACHE or {}).get("history", []))
        new_open = len(data.get("openPositions", []))
        new_hist = len(data.get("history", []))

        if not force and (curr_open > 0 or curr_hist > 0) and (new_open == 0 and new_hist == 0):
            print("🛑 [CRITICAL GUARD] Attempted to wipe out real_positions with empty lists! Save blocked.")
            return

        data["updatedAt"] = datetime.now().isoformat()
        _REAL_POS_CACHE = data

        tmp_file = REAL_POS_FILE + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, REAL_POS_FILE)
            # Sync backup and seed files
            try:
                import shutil
                shutil.copyfile(REAL_POS_FILE, REAL_POS_BAK_FILE)
                shutil.copyfile(REAL_POS_FILE, REAL_POS_SEED_FILE)
            except Exception:
                pass
        except Exception as e:
            print(f"Error saving real positions: {e}")

_LAST_BYBIT_SYNC = 0
_LAST_WALLET_RECONCILE = 0

def reconcile_positions_with_bybit_wallet():
    global _LAST_WALLET_RECONCILE
    now = time.time()
    if now - _LAST_WALLET_RECONCILE < 12:
        return
    _LAST_WALLET_RECONCILE = now

    cfg = load_bybit_config()
    if not cfg:
        return

    try:
        wb_res = bybit_signed_request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
        if wb_res.get("retCode") != 0:
            return
        coins = wb_res.get("result", {}).get("list", [{}])[0].get("coin", [])
        wallet_coins = {}
        for c in coins:
            bal = float(c.get("walletBalance", 0) or 0)
            usd = float(c.get("usdValue", 0) or 0)
            coin_name = c.get("coin", "")
            if usd > 1.0 or bal > 0.001:
                wallet_coins[coin_name] = {
                    "balance": bal,
                    "locked": float(c.get("locked", 0) or 0),
                    "usdValue": usd
                }

        orders_res = bybit_signed_request("GET", "/v5/order/realtime", params={"category": "spot"})
        open_orders = orders_res.get("result", {}).get("list", []) if orders_res.get("retCode") == 0 else []
        orders_by_sym = {}
        for o in open_orders:
            orders_by_sym.setdefault(o["symbol"], []).append(o)

        exec_res = bybit_signed_request("GET", "/v5/execution/list", params={"category": "spot", "limit": "100"})
        exec_list = exec_res.get("result", {}).get("list", []) if exec_res.get("retCode") == 0 else []
        buys_by_sym = {}
        for e in exec_list:
            if e.get("side") == "Buy":
                buys_by_sym.setdefault(e["symbol"], []).append(e)

        acc = load_real_positions()
        open_pos = acc.get("openPositions", [])
        history = acc.get("history", [])
        changed = False

        existing_syms = set()
        cleaned_open_pos = []
        for p in open_pos:
            sym = p.get("symbol")
            base = p.get("baseCoin", sym.replace("USDT", "") if sym else "")
            w_info = wallet_coins.get(base, {})
            w_usd = w_info.get("usdValue", 0)
            has_orders = len(orders_by_sym.get(sym, [])) > 0
            if w_usd < 0.50 and not has_orders:
                changed = True
                continue
            cleaned_open_pos.append(p)
            existing_syms.add(sym)

        open_pos = cleaned_open_pos

        EXCLUDED_COINS = {"USDT", "USDC", "USDE", "FDUSD", "EUR", "DAI", "FHE"}
        for coin_name, info in wallet_coins.items():
            if coin_name in EXCLUDED_COINS:
                continue
            sym = f"{coin_name}USDT"
            usd_val = info["usdValue"]
            bal = info["balance"]

            if usd_val < 3.0:
                continue

            sym_orders = orders_by_sym.get(sym, [])
            sym_buys = buys_by_sym.get(sym, [])

            if not sym_orders and not sym_buys:
                continue

            if sym not in existing_syms:
                entry_p = 0.0
                opened_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                tot_qty = 0.0
                tot_cost = 0.0
                if sym_buys:
                    for b in sym_buys[:5]:
                        b_qty = float(b.get("execQty", 0) or 0)
                        b_price = float(b.get("execPrice", 0) or 0)
                        b_val = float(b.get("execValue", 0) or 0)
                        tot_qty += b_qty
                        tot_cost += b_val if b_val > 0 else (b_qty * b_price)
                    if tot_qty > 0:
                        entry_p = round(tot_cost / tot_qty, 6)
                    t_ms = int(sym_buys[0].get("execTime", 0) or 0)
                    if t_ms:
                        opened_at = datetime.fromtimestamp(t_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")

                if entry_p <= 0:
                    entry_p = round(usd_val / bal, 6) if bal > 0 else 1.0

                sell_orders = [o for o in sym_orders if o.get("side") == "Sell" and o.get("orderType") == "Limit"]
                sell_orders.sort(key=lambda x: float(x.get("price", 0) or 0))

                tp1 = float(sell_orders[0]["price"]) if len(sell_orders) > 0 else round(entry_p * 1.05, 4)
                tp2 = float(sell_orders[1]["price"]) if len(sell_orders) > 1 else round(entry_p * 1.10, 4)
                tp_order_ids = ", ".join([o["orderId"] for o in sell_orders]) if sell_orders else None
                sl = round(entry_p * 0.95, 6)

                new_pos = {
                    "id": f"REAL-{int(time.time())}-{coin_name[:4]}",
                    "symbol": sym,
                    "baseCoin": coin_name,
                    "side": "Buy",
                    "orderType": "Market",
                    "entryPrice": entry_p,
                    "currentPrice": entry_p,
                    "qty": round(bal, 4),
                    "amountUsdt": round(tot_cost if tot_cost > 0 else (bal * entry_p), 2),
                    "feePaid": round(bal * entry_p * 0.001, 3),
                    "currentValue": round(usd_val, 2),
                    "unrealizedPnL": round(usd_val - (tot_cost if tot_cost > 0 else (bal * entry_p)), 2),
                    "unrealizedPnLPct": 0.0,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": None,
                    "sl": sl,
                    "setupName": "صفقة حقيقية Bybit",
                    "openedAt": opened_at,
                    "status": "OPEN",
                    "orderId": sym_buys[0].get("orderId", "") if sym_buys else "",
                    "tpOrderId": tp_order_ids
                }
                open_pos.insert(0, new_pos)
                existing_syms.add(sym)
                changed = True
                print(f"♻️ [Auto-Reconcile] Restored active Bybit position: {sym} (Bal: {bal}, Entry: {entry_p})")

        if changed:
            acc["openPositions"] = open_pos
            acc["history"] = history
            save_real_positions(acc)
    except Exception as ex:
        print(f"⚠️ Error during Bybit wallet reconciliation: {ex}")

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
        if exec_list:
            acc = load_real_positions()
            open_pos = acc.get("openPositions", [])
            history = acc.get("history", [])
            changed = False

            recorded_exec_ids = set(acc.get("ignoredExecIds", []))
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
                    raw_entry = matched_pos.get("entryPrice")
                    entry_price = float(raw_entry or 0)
                    if entry_price <= 0:
                        entry_price = float(matched_pos.get("currentPrice", 0) or exec_price)
                    if entry_price <= 0:
                        entry_price = exec_price
                    cost = round(exec_qty * entry_price, 2)
                    pnl = round(exec_val - cost, 2)
                    pnl_pct = round(((exec_price - entry_price) / entry_price) * 100, 2) if entry_price > 0 else 0.0

                    tp2_target = float(matched_pos.get("tp2", 0) or 0)
                    tp1_target = float(matched_pos.get("tp1", 0) or 0)
                    is_tp2 = tp2_target > 0 and exec_price >= tp2_target * 0.985
                    is_tp1 = (not is_tp2) and (tp1_target > 0 and exec_price >= tp1_target * 0.985)
                    if is_tp2:
                        exit_reason = f"تحقيق الهدف الثاني (TP2: ${exec_price}) وتسييل الصفقة 🎯🚀"
                    elif is_tp1 or pnl > 0:
                        exit_reason = f"تحقيق الهدف الأول (TP1: ${exec_price}) | بيع 50% وتأمين الدخول 🎯"
                    else:
                        exit_reason = f"بيع / وقف خسارة (${exec_price}) 🛑"

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
                        async_telegram_alert(
                            f"🚀🚀 <b>تحقيق الهدف الثاني (TP2) وإغلاق الصفقة بالكامل!</b>\n\n"
                            f"🪙 <b>العملة:</b> <code>{sym}</code>\n"
                            f"💵 <b>سعر التنفيذ:</b> <code>${exec_price:,.4f}</code>\n"
                            f"💰 <b>الربح المحقق:</b> <b>+{pnl:,.2f} USDT (+{pnl_pct}%)</b>\n"
                            f"🎯 <b>العائد الإجمالي:</b> ${exec_val:,.2f} USDT\n"
                            f"✨ <i>ألف مبروك! اكتملت أهداف الصفقة 100% بنجاح.</i>"
                        )
                    else:
                        be_price = bot_protection.breakeven_price(entry_price)
                        matched_pos["qty"] = round(remaining_qty, 4)
                        matched_pos["amountUsdt"] = round(max(0.0, float(matched_pos.get("amountUsdt", 0)) - cost), 2)
                        matched_pos["tp1Executed"] = True
                        matched_pos["tp1LockedPnL"] = pnl
                        if not matched_pos.get("originalSl"):
                            matched_pos["originalSl"] = float(matched_pos.get("sl") or 0)
                        matched_pos["sl"] = max(float(matched_pos.get("sl") or 0), be_price)
                        matched_pos["protectionStage"] = "BREAKEVEN"
                        matched_pos["setupName"] = f"صفقة حقيقية Bybit (تحقق الهدف الأول 🎯)"
                        async_telegram_alert(
                            f"🎯 <b>تحقيق الهدف الأول (TP1) بنجاح!</b>\n\n"
                            f"🪙 <b>العملة:</b> <code>{sym}</code>\n"
                            f"💵 <b>سعر البيع (50%):</b> <code>${exec_price:,.4f}</code>\n"
                            f"💰 <b>الربح المحقق الآن:</b> <b>+{pnl:,.2f} USDT (+{pnl_pct}%)</b>\n"
                            f"🛡️ <b>تأمين الصفقة:</b> تم رفع وقف الخسارة لسعر الدخول + عمولة المنصة (<code>${matched_pos['sl']:,.4f}</code>)\n"
                            f"🚀 متبقي 50% مستمرة نحو الهدف الثاني (TP2: ${tp2_target:,.4f})!"
                        )
                    changed = True

            if changed:
                acc["openPositions"] = open_pos
                acc["history"] = history
                save_real_positions(acc)
    except Exception as ex:
        print(f"⚠️ Error syncing Bybit real executions: {ex}")

    # Auto-reconcile positions with Bybit wallet balances & active orders
    reconcile_positions_with_bybit_wallet()

def get_real_positions_summary():
    sync_real_positions_with_bybit()
    acc = load_real_positions()
    if heal_and_recalc_history(acc):
        save_real_positions(acc)
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
    winning_trades = sum(1 for h in history if float(h.get("realizedPnL", 0)) > 0.005)
    losing_trades = sum(1 for h in history if float(h.get("realizedPnL", 0)) < -0.005)
    win_rate = round((winning_trades / total_trades * 100), 1) if total_trades > 0 else 0.0
    total_realized_pnl = round(sum(float(h.get("realizedPnL", 0)) for h in history), 2)
    today_pnl = bot_protection.realized_pnl_today(history)
    prot_status = bot_protection.get_status()
    
    return {
        "retCode": 0,
        "openPositions": open_pos,
        "history": history,
        "totalInvested": total_invested,
        "totalValue": total_pos_value,
        "totalUnrealizedPnL": total_unrealized_pnl,
        "protection": prot_status,
        "analytics": bot_protection.compute_journal_analytics(history),
        "stats": {
            "totalTrades": total_trades,
            "winningTrades": winning_trades,
            "losingTrades": losing_trades,
            "winRate": win_rate,
            "totalRealizedPnL": total_realized_pnl,
            "todayRealizedPnL": today_pnl
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

        if path == "/api/ping":
            self._send_json({"status": "ok", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "service": "bybit-screener"})
            return

        elif path == "/api/test-telegram":
            ok = send_telegram_alert(
                "⚡ <b>فحص الاتصال الفوري:</b>\n\n"
                "📱 نظام إشعارات Bybit Spot متصل بنجاح مع هاتفك عبر تيليجرام!\n\n"
                "• 🛒 تنبيهات الصفقات المفتوحة فورياً\n"
                "• 🎯 أهداف جني الأرباح (TP1 / TP2)\n"
                "• 🛡️ حارس وقف الخسارة وتأمين الدخول (SL Guard 24/7)"
            )
            self._send_json({"success": ok, "message": "تم إرسال إشعار التجربة لهاتفك بنجاح!" if ok else "فشل الإرسال"})
            return

        elif path == "/api/scan":
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

        elif path == "/api/market-regime":
            regime = get_market_regime()
            self._send_json(regime)
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

        elif path == "/api/protection/status":
            real_acc = load_real_positions()
            hist = real_acc.get("history", [])
            status = bot_protection.get_status()
            status["todayRealizedPnL"] = bot_protection.realized_pnl_today(hist)
            self._send_json({"retCode": 0, **status})
            return

        elif path == "/api/backtest":
            try:
                import backtest_engine
                qs = urllib.parse.parse_qs(parsed.query)
                interval = qs.get("interval", ["240"])[0]
                limit = int(qs.get("limit", ["500"])[0])
                syms_param = qs.get("symbols", [None])[0]
                symbols_list = [s.strip().upper() for s in syms_param.split(",") if s.strip()] if syms_param else None
                report = backtest_engine.run_portfolio_backtest(symbols=symbols_list, interval=interval, limit=limit)
                self._send_json(report)
            except Exception as bt_err:
                self._send_json({"retCode": -1, "retMsg": f"خطأ أثناء تشغيل الاختبار التاريخي: {bt_err}"})
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

        elif path == "/api/protection/settings":
            updated = bot_protection.save_settings(req_json)
            self._send_json({"retCode": 0, "success": True, "settings": updated})
            return

        elif path == "/api/protection/unlock-daily":
            bot_protection.reset_daily_lock()
            self._send_json({"retCode": 0, "success": True, "message": "تم رفع قفل الخسارة اليومية يدوياً."})
            return

        elif path == "/api/autotrader/run-now":
            try:
                res_auto = execute_auto_trade_cycle(force_run=True)
                self._send_json({"retCode": 0, "success": True, **res_auto})
            except Exception as ex_auto:
                self._send_json({"retCode": -1, "success": False, "error": str(ex_auto)})
            return

        elif path == "/api/order/place":
            symbol = req_json.get("symbol", "").upper()
            side = req_json.get("side", "Buy").capitalize()
            order_type = req_json.get("orderType", "Market").capitalize()
            qty = str(req_json.get("qty", ""))
            price = str(req_json.get("price", "")) if req_json.get("price") else None

            # فحص قاطع الدائرة للخسارة اليومية قبل أي أمر شراء جديد
            if side == "Buy":
                try:
                    real_acc_chk = load_real_positions()
                    hist_chk = real_acc_chk.get("history", [])
                    open_chk = real_acc_chk.get("openPositions", [])
                    prot_cfg = bot_protection.load_settings()

                    # التحقق من الحد الأقصى للصفقات المفتوحة
                    max_open = int(prot_cfg.get("maxOpenPositions", 4) or 4)
                    if len(open_chk) >= max_open and not req_json.get("forceOverride"):
                        self._send_json({
                            "retCode": -1002,
                            "retMsg": f"🛡️ حماية المحفظة: لديك حالياً {len(open_chk)} صفقات مفتوحة (الحد الأقصى الآمن: {max_open} صفقات)."
                        })
                        return

                    def _estimate_equity():
                        invested = sum(float(p.get("amountUsdt", 0) or 0) for p in open_chk)
                        try:
                            wb_eq = bybit_signed_request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
                            if wb_eq.get("retCode") == 0:
                                tot = float(wb_eq.get("result", {}).get("list", [{}])[0].get("totalEquity", 0) or 0)
                                if tot > 0:
                                    return tot
                        except Exception:
                            pass
                        return max(invested, 100.0)

                    dl_info = bot_protection.check_daily_loss(hist_chk, equity_fn=_estimate_equity)
                    if dl_info.get("locked") and not req_json.get("forceOverride"):
                        self._send_json({
                            "retCode": -1001,
                            "retMsg": f"🛑 قاطع الخسارة اليومية مفعّل: {dl_info.get('reason')} — تم إيقاف فتح صفقات شراء جديدة اليوم لحماية رأس المال."
                        })
                        return
                except Exception as guard_chk_err:
                    print(f"⚠️ [Daily Loss Check Error] {guard_chk_err}")
            
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
                    if not exec_qty:
                        try:
                            # 1. Fallback: check Bybit execution list for order_id
                            ex_res = bybit_signed_request("GET", "/v5/execution/list", params={"category": "spot", "orderId": order_id})
                            ex_list = ex_res.get("result", {}).get("list", [])
                            if ex_list:
                                exec_qty = sum(float(e.get("execQty", 0) or 0) for e in ex_list)
                                total_val = sum(float(e.get("execValue", 0) or 0) for e in ex_list)
                                if exec_qty > 0 and not exec_price:
                                    exec_price = total_val / exec_qty
                        except Exception:
                            pass
                    if not exec_qty:
                        try:
                            # 2. Fallback: check wallet balance
                            base_coin = symbol.replace("USDT", "")
                            wb = bybit_signed_request("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED", "coin": base_coin})
                            c_list = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
                            if c_list:
                                w_bal = float(c_list[0].get("walletBalance", 0) or 0)
                                if w_bal > 0:
                                    exec_qty = w_bal
                        except Exception:
                            pass
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
                    entry_p = exec_price or float(orders[0].get("basePrice", 0) if orders else 0)
                    if not entry_p or entry_p <= 0:
                        try:
                            tickers = get_all_spot_tickers_cached(ttl=2.0)
                            entry_p = float(tickers.get(symbol, 0) or 0)
                        except Exception:
                            pass
                    if not entry_p or entry_p <= 0:
                        entry_p = float(req_json.get("price") or 0)

                    prot_settings = bot_protection.load_settings()
                    default_sl_pct = float(prot_settings.get("defaultStopLossPct", 4.2) or 4.2) / 100.0
                    effective_sl = float(sl) if (sl and float(sl) > 0) else (round(entry_p * (1.0 - default_sl_pct), 6) if entry_p > 0 else None)

                    new_real_pos = {
                        "id": f"REAL-{int(time.time())}-{symbol[:4]}",
                        "symbol": symbol,
                        "baseCoin": symbol.replace("USDT", ""),
                        "side": "Buy",
                        "orderType": order_type,
                        "entryPrice": entry_p,
                        "currentPrice": entry_p,
                        "highestPrice": entry_p,
                        "qty": exec_qty,
                        "amountUsdt": float(qty) if (order_type == "Market" and req_json.get("marketUnit") == "quoteCoin") else round(exec_qty * entry_p, 2),
                        "feePaid": round(exec_qty * entry_p * 0.001, 3),
                        "currentValue": round(exec_qty * entry_p, 2),
                        "unrealizedPnL": 0.0,
                        "unrealizedPnLPct": 0.0,
                        "tp1": float(req_json.get("tp1", 0)) if req_json.get("tp1") else (float(tp) if tp else None),
                        "tp2": float(req_json.get("tp2", 0)) if req_json.get("tp2") else (float(tp) if tp else None),
                        "tp3": float(req_json.get("tp3", 0)) if req_json.get("tp3") else None,
                        "sl": effective_sl,
                        "originalSl": effective_sl,
                        "protectionStage": "INITIAL",
                        "setupName": req_json.get("setupName", "صفقة حقيقية Bybit"),
                        "openedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "OPEN",
                        "orderId": order_id,
                        "tpOrderId": tp_order_id
                    }
                    real_acc["openPositions"].insert(0, new_real_pos)
                    save_real_positions(real_acc)
                    # إشعار تيليجرام الفوري عند فتح صفقة جديدة
                    async_telegram_alert(
                        f"🛒 <b>تم فتح صفقة جديدة على منصة Bybit!</b>\n\n"
                        f"🪙 <b>العملة:</b> <code>{symbol}</code>\n"
                        f"📈 <b>سعر الدخول:</b> <code>${new_real_pos['entryPrice']:,.4f}</code>\n"
                        f"💵 <b>المبلغ المستثمر:</b> <code>${new_real_pos['amountUsdt']:,.2f} USDT</code>\n"
                        f"🎯 <b>الهدف الأول (TP1):</b> <code>${new_real_pos.get('tp1', 'N/A')}</code>\n"
                        f"🚀 <b>الهدف الثاني (TP2):</b> <code>${new_real_pos.get('tp2', 'N/A')}</code>\n"
                        f"🛡️ <b>وقف الخسارة (SL):</b> <code>${new_real_pos.get('sl', 'N/A')}</code>\n"
                        f"⚙️ <b>النموذج الفني:</b> {new_real_pos.get('setupName', 'صفقة فورية Bybit')}"
                    )
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
                tickers = get_all_spot_tickers_cached(ttl=2.0)
                live_price = tickers.get(sym, float(pos.get("entryPrice", 0)))
                entry_p = float(pos.get("entryPrice", live_price) or live_price)
                p_qty = float(qty or 0)
                p_cost = float(pos.get("amountUsdt", 0) or (p_qty * entry_p))
                net_ret = round(p_qty * live_price, 2)
                pnl = round(net_ret - p_cost, 2)
                pnl_pct = round(((live_price - entry_p) / entry_p) * 100, 2) if entry_p > 0 else 0.0

                real_acc["openPositions"] = [p for p in real_acc["openPositions"] if p["id"] != pos_id]
                pos["status"] = "CLOSED"
                pos["closeReason"] = "MANUAL_CLOSE"
                pos["closePrice"] = live_price
                pos["netReturn"] = net_ret
                pos["realizedPnL"] = pnl
                pos["pnlPct"] = pnl_pct
                pos["exitReason"] = f"إغلاق يدوي فوري (${live_price:,.4f}) ⚡"
                pos["closedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                real_acc.setdefault("history", []).insert(0, pos)
                save_real_positions(real_acc)
            self._send_json(sell_res)
            return

        elif path == "/api/real/history/delete":
            item_id = req_json.get("id")
            exec_id = req_json.get("execId")
            order_id = req_json.get("orderId")
            symbol = req_json.get("symbol")
            
            real_acc = load_real_positions()
            history = real_acc.get("history", [])
            initial_count = len(history)
            
            history = [h for h in history if not (
                (item_id and h.get("id") == item_id) or
                (exec_id and h.get("execId") == exec_id) or
                (order_id and h.get("orderId") == order_id)
            )]
            
            if len(history) < initial_count:
                real_acc["history"] = history
                ignored = real_acc.get("ignoredExecIds", [])
                for x in [item_id, exec_id, order_id]:
                    if x and x not in ignored:
                        ignored.append(x)
                real_acc["ignoredExecIds"] = ignored
                
                total_trades = len(history)
                winning_trades = sum(1 for h in history if float(h.get("realizedPnL", 0) or 0) > 0.005)
                losing_trades = sum(1 for h in history if float(h.get("realizedPnL", 0) or 0) < -0.005)
                win_rate = round((winning_trades / total_trades * 100), 1) if total_trades > 0 else 0.0
                total_realized_pnl = round(sum(float(h.get("realizedPnL", 0) or 0) for h in history), 2)
                real_acc["stats"] = {
                    "totalTrades": total_trades,
                    "winningTrades": winning_trades,
                    "losingTrades": losing_trades,
                    "winRate": win_rate,
                    "totalRealizedPnL": total_realized_pnl
                }
                save_real_positions(real_acc)
                self._send_json({"success": True, "message": f"تم حذف صفقة {symbol or ''} من السجل بنجاح", "stats": real_acc["stats"]})
            else:
                self._send_json({"success": False, "message": "لم يتم العثور على الصفقة في السجل"})
            return

        elif path == "/api/real/history/clear-all":
            with REAL_POS_LOCK:
                real_acc = load_real_positions()
                history = real_acc.get("history", [])
                ignored = set(real_acc.get("ignoredExecIds", []))
                for h in history:
                    for k in ["id", "execId", "orderId"]:
                        v = h.get(k)
                        if v:
                            ignored.add(str(v))
                real_acc["ignoredExecIds"] = list(ignored)
                real_acc["history"] = []
                real_acc["stats"] = {
                    "totalTrades": 0,
                    "winningTrades": 0,
                    "losingTrades": 0,
                    "winRate": 0.0,
                    "totalRealizedPnL": 0.0
                }
                save_real_positions(real_acc, force=True)
                self._send_json({
                    "success": True,
                    "message": "تم مسح سجل الصفقات بالكامل بنجاح وتصفير الإحصائيات",
                    "stats": real_acc["stats"]
                })
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
                "highestPrice": price,
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
                "originalSl": sl,
                "protectionStage": "INITIAL",
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
            bot_protection.mark_guard_ok()

            # 1. Automatically sync filled TP limit orders directly from Bybit's live trades
            sync_real_positions_with_bybit()

            acc = load_real_positions()
            open_pos = acc.get("openPositions", [])
            hist = acc.get("history", [])

            # Check daily loss circuit breaker automatically
            try:
                bot_protection.check_daily_loss(hist)
            except Exception:
                pass

            if not open_pos:
                continue
            tickers = get_all_spot_tickers_cached(ttl=2.0)
            prot_settings = bot_protection.load_settings()
            changed = False
            for pos in list(open_pos):
                sym = pos.get("symbol")
                live_price = tickers.get(sym)
                if not live_price or live_price <= 0:
                    continue

                # 1. Update dynamic protective stop (Breakeven + Fees / Lock TP1 / Trailing Stop)
                upd = bot_protection.compute_protective_stop(pos, live_price, prot_settings)
                prev_stage = pos.get("protectionStage", "INITIAL")
                prev_high = float(pos.get("highestPrice") or 0)
                if upd["highest"] > prev_high * 1.002:
                    pos["highestPrice"] = upd["highest"]
                    pos["originalSl"] = upd["originalSl"]
                    changed = True

                if upd["changed"]:
                    old_sl = float(pos.get("sl") or 0)
                    pos["sl"] = upd["sl"]
                    pos["originalSl"] = upd["originalSl"]
                    pos["highestPrice"] = upd["highest"]
                    pos["protectionStage"] = upd["stage"]
                    changed = True

                    # إرسال إشعار تيليجرام عند ترقية مرحلة الحماية (أو تحرك التريلينج ستوب بنسبة مؤثرة >= 0.6%)
                    stage_changed = (upd["stage"] != prev_stage)
                    sl_jump_pct = ((upd["sl"] - old_sl) / old_sl * 100.0) if old_sl > 0 else 100.0
                    if stage_changed or sl_jump_pct >= 0.6:
                        async_telegram_alert(
                            f"🛡️ <b>ترقية حماية الصفقة تلقائياً ({upd['stage']})!</b>\n\n"
                            f"🪙 <b>العملة:</b> <code>{sym}</code>\n"
                            f"📈 <b>السعر الحالي:</b> <code>${live_price:,.4f}</code> (أعلى قمة: <code>${upd['highest']:,.4f}</code>)\n"
                            f"🔒 <b>وقف الخسارة الجديد:</b> <code>${upd['sl']:,.4f}</code> (كان: <code>${old_sl:,.4f}</code>)\n"
                            f"✨ <b>السبب:</b> {upd['reason']}"
                        )

                sl = float(pos.get("sl") or 0)
                if sl > 0 and live_price <= sl:
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
                        bot_protection.report_error(f"sl_sell_{sym}", f"فشل تنفيذ أمر بيع وقف الخسارة للعملة {sym}", sl_err)

                    # 3. Compute Realized PnL and move position to history
                    entry_p = float(pos.get("entryPrice", live_price) or live_price)
                    p_qty = float(pos.get("qty", 0) or 0)
                    p_cost = round(p_qty * entry_p, 2) if (p_qty > 0 and entry_p > 0) else float(pos.get("amountUsdt", 0) or 0)
                    net_ret = round(p_qty * live_price, 2)
                    pnl = round(net_ret - p_cost, 2)
                    pnl_pct = round(((live_price - entry_p) / entry_p) * 100, 2) if entry_p > 0 else 0.0
                    stage_now = pos.get("protectionStage", "INITIAL")

                    open_pos.remove(pos)
                    pos["amountUsdt"] = p_cost
                    pos["status"] = "CLOSED"
                    pos["closeReason"] = "TRAILING_PROFIT_HIT" if pnl >= 0 else "STOP_LOSS_HIT"
                    pos["closePrice"] = live_price
                    pos["netReturn"] = net_ret
                    pos["realizedPnL"] = pnl
                    pos["pnlPct"] = pnl_pct
                    if pnl >= 0:
                        pos["exitReason"] = f"تأمين أرباح تلقائي ({stage_now} @ ${sl:,.4f}) 🛡️✅"
                    else:
                        pos["exitReason"] = f"تفعيل وقف الخسارة الإلزامي (${sl:,.4f}) 🛑"
                    pos["closedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    acc.setdefault("history", []).insert(0, pos)
                    changed = True

                    # فحص قاطع الدائرة اليومي فوراً بعد إغلاق الصفقة
                    try:
                        bot_protection.check_daily_loss(acc.get("history", []))
                    except Exception:
                        pass

                    # إشعار تيليجرام لتفعيل وقف الخسارة أو تأمين الربح
                    if pnl >= 0:
                        async_telegram_alert(
                            f"🛡️✅ <b>تم إغلاق الصفقة على ربح مؤمّن ({stage_now})!</b>\n\n"
                            f"🪙 <b>العملة:</b> <code>{sym}</code>\n"
                            f"📈 <b>سعر الخروج:</b> <code>${live_price:,.4f}</code> (وقف متحرك: ${sl:,.4f})\n"
                            f"💰 <b>الربح الصافي:</b> <b>+{pnl:,.2f} USDT ({pnl_pct:+.2f}%)</b>\n"
                            f"💵 <b>المبلغ المسترد للكاش:</b> ${net_ret:,.2f} USDT"
                        )
                    else:
                        async_telegram_alert(
                            f"🛑 <b>تنبيه تفعيل وقف الخسارة (Stop-Loss Guard)!</b>\n\n"
                            f"🪙 <b>العملة:</b> <code>{sym}</code>\n"
                            f"📉 <b>سعر الخروج:</b> <code>${live_price:,.4f}</code> (حد الأمان: ${sl:,.4f})\n"
                            f"💸 <b>الخسارة المحققة:</b> <b>{pnl:,.2f} USDT ({pnl_pct:+.2f}%)</b>\n"
                            f"💵 <b>المبلغ المسترد للكاش:</b> ${net_ret:,.2f} USDT\n"
                            f"⚡ <i>تم تسييل الصفقة بالكامل بالسوق لحماية المحفظة ومنع تعميق الخسارة.</i>"
                        )
            if changed:
                save_real_positions(acc)
        except Exception as e:
            bot_protection.report_error("sl_guard_loop", "حدث خطأ غير متوقع في حلقة حارس وقف الخسارة", e)

def execute_auto_trade_cycle(force_run: bool = False) -> dict:
    """
    ينفذ دورة فحص وتداول آلي ذكي واحدة وفق الضوابط الصارمة:
    - لا يدخل إلا الصفقات التي تحقق minScore (افتراضياً 86+).
    - يحترم قاطع الخسارة اليومي، الحد الأقصى للصفقات المفتوحة، والحد اليومي للصفقات الآلية.
    - يمنع تكرار الدخول في نفس العملة أو خلال فترة التهدئة (Cooldown).
    """
    settings = bot_protection.load_settings()
    if not settings.get("autoTradeEnabled", False) and not force_run:
        bot_protection.update_auto_trader_heartbeat("متوقف (يدوي)")
        return {"executed": [], "status": "DISABLED"}

    mode = str(settings.get("autoTradeMode", "demo")).lower()
    min_score = int(settings.get("autoTradeMinScore", 86) or 86)
    amount_usdt = float(settings.get("autoTradeAmountUsdt", 15.0) or 15.0)
    max_open = int(settings.get("maxOpenPositions", 4) or 4)
    require_safe_mkt = bool(settings.get("autoTradeRequireSafeMarket", True))

    # 1. فحص حالة السوق العام (BTC Regime)
    regime = get_market_regime()
    if require_safe_mkt and regime.get("status") == "BEARISH":
        msg = "تم تعليق الدخول الآلي مؤقتاً لأن البيتكوين في مسار هابط (Risk-Off) 🛡️"
        bot_protection.update_auto_trader_heartbeat(msg)
        return {"executed": [], "status": "MARKET_BEARISH", "message": msg}

    # 2. فحص الصفقات المفتوحة حالياً وقاطع الخسارة اليومي
    if mode == "live":
        real_acc = load_real_positions()
        open_pos = real_acc.get("openPositions", [])
        hist = real_acc.get("history", [])
        dl = bot_protection.check_daily_loss(hist)
        if dl.get("locked"):
            msg = f"قاطع الخسارة اليومي مفعّل: {dl.get('reason')}"
            bot_protection.update_auto_trader_heartbeat(msg)
            return {"executed": [], "status": "DAILY_LOCKED", "message": msg}
    else:
        demo_acc = load_demo_account()
        open_pos = demo_acc.get("openPositions", [])

    if len(open_pos) >= max_open:
        msg = f"المحفظة وصلت للحد الأقصى الآمن من الصفقات المفتوحة ({len(open_pos)}/{max_open})"
        bot_protection.update_auto_trader_heartbeat(msg)
        return {"executed": [], "status": "MAX_OPEN_REACHED", "message": msg}

    open_symbols = {p.get("symbol") for p in open_pos}

    # 3. الحصول على نتائج الفحص اللحظي أو تشغيل فحص جديد
    global CACHE_DATA, CACHE_TIME
    if not CACHE_DATA or (time.time() - CACHE_TIME > 180):
        scan_data = run_screener_scan(interval="240", max_pairs=70)
    else:
        scan_data = CACHE_DATA

    setups = (scan_data or {}).get("setups") or []
    candidates = [
        s for s in setups
        if int(s.get("score", 0)) >= min_score and s.get("symbol") not in open_symbols
    ]
    candidates.sort(key=lambda x: (x.get("score", 0), x.get("rr_ratio", 0)), reverse=True)

    if not candidates:
        msg = f"لا توجد فرص نخبة تحقق شرط التقييم ({min_score}+) حالياً"
        bot_protection.update_auto_trader_heartbeat(msg)
        return {"executed": [], "status": "NO_QUALIFIED_SETUPS", "message": msg}

    executed_list = []
    slots_left = max_open - len(open_pos)

    for cand in candidates:
        if slots_left <= 0:
            break
        sym = cand["symbol"]
        allowed, reason = bot_protection.can_auto_trade_symbol(sym, settings)
        if not allowed:
            continue

        entry_p = float(cand["price"])
        tp1 = float(cand["tp1"])
        tp2 = float(cand["tp2"])
        tp3 = float(cand.get("tp3") or tp2)
        sl = float(cand["sl"])
        score = int(cand["score"])
        setup_name = f"🤖 آلي: {cand['setup_name']}"

        if mode == "demo":
            demo_acc = load_demo_account()
            bal = float(demo_acc.get("balanceUsdt", 0) or 0)
            if bal < amount_usdt:
                bot_protection.update_auto_trader_heartbeat(f"رصيد المحاكاة غير كافٍ (${bal:.2f})")
                break
            fee = round(amount_usdt * 0.001, 4)
            qty_coins = round((amount_usdt - fee) / entry_p, 6)
            pos_id = f"AUTO-DEMO-{int(time.time())}-{sym[:4]}"
            new_pos = {
                "id": pos_id,
                "symbol": sym,
                "baseCoin": sym.replace("USDT", ""),
                "side": "Buy",
                "orderType": "Market",
                "entryPrice": entry_p,
                "currentPrice": entry_p,
                "highestPrice": entry_p,
                "qty": qty_coins,
                "amountUsdt": amount_usdt,
                "feePaid": fee,
                "currentValue": amount_usdt,
                "unrealizedPnL": 0.0,
                "unrealizedPnLPct": 0.0,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "sl": sl,
                "originalSl": sl,
                "protectionStage": "INITIAL",
                "tpStrategy": "TP1_TP2",
                "setupName": setup_name,
                "score": score,
                "openedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "OPEN",
            }
            demo_acc["balanceUsdt"] = round(bal - amount_usdt, 2)
            demo_acc.setdefault("openPositions", []).insert(0, new_pos)
            save_demo_account(demo_acc)
            bot_protection.record_auto_trade_execution(sym, "DEMO", entry_p, amount_usdt, score, setup_name)
            executed_list.append(new_pos)
            slots_left -= 1
            async_telegram_alert(
                f"🤖🎮 <b>صفقة آلية جديدة (وضع المحاكاة الذكي)!</b>\n\n"
                f"🪙 <b>العملة:</b> <code>{sym}</code> (تقييم: <b>{score}/98</b>)\n"
                f"📈 <b>سعر الدخول:</b> <code>${entry_p:,.4f}</code>\n"
                f"💵 <b>المبلغ:</b> <code>${amount_usdt:,.2f} USDT</code>\n"
                f"🎯 <b>الهدف الأول (TP1):</b> <code>${tp1:,.4f}</code>\n"
                f"🚀 <b>الهدف الثاني (TP2):</b> <code>${tp2:,.4f}</code>\n"
                f"🛡️ <b>وقف الخسارة (SL):</b> <code>${sl:,.4f}</code>\n"
                f"⚙️ <b>النموذج:</b> {cand['setup_name']}"
            )

        elif mode == "live":
            # تنفيذ حقيقي على Bybit Spot مع تقسيم الأهداف 50% TP1 و 50% TP2
            info = get_instrument_info(sym)
            tick_size = info.get("priceFilter", {}).get("tickSize", "0.0001")
            base_prec = info.get("lotSizeFilter", {}).get("basePrecision", "0.01")

            buy_body = {
                "category": "spot",
                "symbol": sym,
                "side": "Buy",
                "orderType": "Market",
                "marketUnit": "quoteCoin",
                "qty": f"{amount_usdt:.2f}",
            }
            res = bybit_signed_request("POST", "/v5/order/create", json_body=buy_body)
            if res.get("retCode") != 0:
                bot_protection.report_error(f"auto_buy_{sym}", f"تعذر تنفيذ شراء آلي للعملة {sym}: {res.get('retMsg')}")
                continue

            order_id = res.get("result", {}).get("orderId")
            time.sleep(0.8)
            exec_qty = 0.0
            exec_price = entry_p
            try:
                ex_res = bybit_signed_request("GET", "/v5/execution/list", params={"category": "spot", "orderId": order_id})
                ex_list = ex_res.get("result", {}).get("list", [])
                if ex_list:
                    exec_qty = sum(float(e.get("execQty", 0) or 0) for e in ex_list)
                    tot_v = sum(float(e.get("execValue", 0) or 0) for e in ex_list)
                    if exec_qty > 0:
                        exec_price = tot_v / exec_qty
            except Exception:
                pass
            if exec_qty <= 0:
                exec_qty = (amount_usdt * 0.999) / entry_p

            sell_qty = exec_qty * 0.999
            half1 = float(round_to_step(sell_qty / 2.0, base_prec, rounding=ROUND_DOWN))
            half2 = float(round_to_step(sell_qty - half1, base_prec, rounding=ROUND_DOWN))
            tp_ids = []
            for h_q, h_tp in [(half1, tp1), (half2, tp2)]:
                if h_q > 0:
                    s_res = bybit_signed_request("POST", "/v5/order/create", json_body={
                        "category": "spot",
                        "symbol": sym,
                        "side": "Sell",
                        "orderType": "Limit",
                        "qty": round_to_step(h_q, base_prec, rounding=ROUND_DOWN),
                        "price": round_to_step(h_tp, tick_size),
                        "timeInForce": "GTC",
                    })
                    if s_res.get("retCode") == 0:
                        tp_ids.append(s_res.get("result", {}).get("orderId"))

            tp_order_id = ", ".join(tp_ids) if tp_ids else None
            real_acc = load_real_positions()
            new_real_pos = {
                "id": f"AUTO-REAL-{int(time.time())}-{sym[:4]}",
                "symbol": sym,
                "baseCoin": sym.replace("USDT", ""),
                "side": "Buy",
                "orderType": "Market",
                "entryPrice": exec_price,
                "currentPrice": exec_price,
                "highestPrice": exec_price,
                "qty": exec_qty,
                "amountUsdt": amount_usdt,
                "feePaid": round(amount_usdt * 0.001, 3),
                "currentValue": amount_usdt,
                "unrealizedPnL": 0.0,
                "unrealizedPnLPct": 0.0,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "sl": sl,
                "originalSl": sl,
                "protectionStage": "INITIAL",
                "setupName": setup_name,
                "score": score,
                "openedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "OPEN",
                "orderId": order_id,
                "tpOrderId": tp_order_id,
            }
            real_acc.setdefault("openPositions", []).insert(0, new_real_pos)
            save_real_positions(real_acc)
            bot_protection.record_auto_trade_execution(sym, "LIVE", exec_price, amount_usdt, score, setup_name)
            executed_list.append(new_real_pos)
            slots_left -= 1
            async_telegram_alert(
                f"🤖🔴 <b>تم تنفيذ صفقة حقيقية آلياً على Bybit!</b>\n\n"
                f"🪙 <b>العملة:</b> <code>{sym}</code> (تقييم: <b>{score}/98</b>)\n"
                f"📈 <b>سعر التنفيذ:</b> <code>${exec_price:,.4f}</code>\n"
                f"💵 <b>المبلغ المستثمر:</b> <code>${amount_usdt:,.2f} USDT</code>\n"
                f"🎯 <b>الهدف الأول (50%):</b> <code>${tp1:,.4f}</code>\n"
                f"🚀 <b>الهدف الثاني (50%):</b> <code>${tp2:,.4f}</code>\n"
                f"🛡️ <b>وقف الخسارة المحمي:</b> <code>${sl:,.4f}</code>\n"
                f"⚙️ <b>النموذج:</b> {cand['setup_name']}"
            )

    if not executed_list:
        bot_protection.update_auto_trader_heartbeat("تم الفحص — لا توجد عملات متاحة خارج التهدئة")

    return {"executed": executed_list, "status": "OK", "count": len(executed_list)}


def background_auto_trader_loop():
    print("🤖 [Smart Auto-Trader] Autonomous background trading engine ready.")
    time.sleep(8.0)
    while True:
        try:
            settings = bot_protection.load_settings()
            interval_sec = max(60, int(settings.get("autoTradeIntervalSec", 300) or 300))
            if settings.get("autoTradeEnabled", False):
                execute_auto_trade_cycle(force_run=False)
            time.sleep(interval_sec)
        except Exception as e:
            bot_protection.report_error("auto_trader_loop", "حدث خطأ في حلقة المتداول الآلي الذكي", e)
            time.sleep(60)


def cloud_keepalive_loop():
    """Keeps the Render cloud service awake by sending an external HTTP ping every 8 minutes."""
    ext_url = os.environ.get("RENDER_EXTERNAL_URL") or "https://pepo-screener-1.onrender.com"
    ping_url = f"{ext_url.rstrip('/')}/api/ping"
    print(f"📡 [Cloud Keepalive] Auto-ping active for 24/7 uptime: {ping_url}")
    time.sleep(30)
    while True:
        try:
            time.sleep(480)  # Ping every 8 minutes (Render idle limit is 15 mins)
            r = requests.get(ping_url, timeout=15)
            if r.status_code == 200:
                print(f"💓 [Keepalive Ping OK] {datetime.now().strftime('%H:%M:%S')}")
        except Exception:
            pass

def open_browser_reliably(url: str):
    """Opens URL in default browser reliably on Windows, even when launched from .bat or background."""
    import subprocess
    import webbrowser
    if sys.platform.startswith("win"):
        try:
            os.startfile(url)
            return
        except Exception:
            pass
        try:
            subprocess.Popen(f'cmd /c start "" "{url}"', shell=True)
            return
        except Exception:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass


def start_server():
    import threading

    # 1. If server is already running, open browser immediately and exit cleanly
    if is_port_responding(PORT):
        print("\n" + "=" * 72)
        print("  ⚡ BYBIT SPOT AI SCREENER PRO")
        print("  ✅ الخادم نشط ويعمل بالفعل في خلفية النظام على المنفذ 5600!")
        print(f"  🌐 جاري فتح واجهة المنصة الآن في متصفحك: http://127.0.0.1:{PORT}/")
        print("=" * 72 + "\n")
        open_browser_reliably(f"http://127.0.0.1:{PORT}/")
        sys.exit(0)

    # 2. Clean up any stale/hung port occupant
    kill_port_owner(PORT)

    # Start background Stop-Loss guard daemon + Watchdog monitor + Smart Auto-Trader
    threading.Thread(target=background_stop_loss_guard, daemon=True).start()
    threading.Thread(target=bot_protection.guard_watchdog_loop, daemon=True).start()
    threading.Thread(target=background_auto_trader_loop, daemon=True).start()

    # Start cloud keep-alive loop on cloud instances to ensure 24/7 wakefulness
    is_cloud = bool(os.environ.get("RENDER") or os.environ.get("KOYEB_APP_NAME") or os.environ.get("PORT"))
    if is_cloud:
        threading.Thread(target=cloud_keepalive_loop, daemon=True).start()

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
    if not is_cloud:
        def launch_browser():
            time.sleep(0.6)
            open_browser_reliably(f"http://127.0.0.1:{PORT}/")
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

