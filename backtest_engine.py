#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
📊 Bybit Spot Historical Backtest Engine (محرك الاختبار التاريخي للاستراتيجيات)
==============================================================================
يختبر نماذج الدخول الأربعة (Golden Pullback, Volume Breakout, Bullish Divergence,
Oversold Bounce) على بيانات الشموع التاريخية الحقيقية (4 ساعات) من منصة Bybit:
- تنفيذ الإشارات على إغلاق الشمعة المؤكدة (Zero Repainting / Zero Lookahead Bias).
- خصم عمولة التداول الفوري الحقيقية (0.1% شراء + 0.1% بيع).
- محاكاة إدارة الصفقة الذكية:
  * بيع 50% عند الهدف الأول (TP1).
  * نقل وقف الخسارة تلقائياً إلى نقطة التعادل + العمولة (+0.25%).
  * تفعيل قفل ربح TP1 والتريلينج ستوب المتحرك (1.0 R) للنصف المتبقي حتى TP2.
==============================================================================
"""

import os
import sys
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import numpy as np

import bot_protection

BYBIT_BASE_URL = "https://api.bybit.com"
SPOT_FEE_RATE = 0.001  # 0.1% spot fee per side


def calculate_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    if len(prices) < period + 1:
        return np.full(len(prices), 50.0)
    deltas = np.diff(prices)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 100.0
    rsi = np.zeros_like(prices)
    rsi[:period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period, len(prices)):
        delta = deltas[i - 1]
        upval = delta if delta > 0 else 0.0
        downval = -delta if delta < 0 else 0.0
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 100.0
        rsi[i] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


def calculate_ema(prices: np.ndarray, period: int) -> np.ndarray:
    if len(prices) < period:
        return prices.copy()
    ema = np.zeros_like(prices)
    ema[:period] = np.mean(prices[:period])
    k = 2.0 / (period + 1)
    for i in range(period, len(prices)):
        ema[i] = prices[i] * k + ema[i - 1] * (1.0 - k)
    return ema


def calculate_macd(prices: np.ndarray):
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    macd_line = ema12 - ema26
    signal_line = calculate_ema(macd_line, 9)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def fetch_historical_klines(symbol: str, interval: str = "240", limit: int = 500) -> list:
    """جلب الشموع التاريخية من Bybit مرتبة من الأقدم للأحدث مع استبعاد الشمعة الحية غير المغلقة."""
    url = f"{BYBIT_BASE_URL}/v5/market/kline?category=spot&symbol={symbol}&interval={interval}&limit={min(limit, 1000)}"
    try:
        r = requests.get(url, timeout=10).json()
        if r.get("retCode") == 0:
            raw_list = r["result"]["list"]
            ordered = raw_list[::-1]
            # استبعاد الشمعة الأخيرة المفتوحة حالياً
            return ordered[:-1] if len(ordered) > 60 else ordered
    except Exception:
        pass
    return []


def evaluate_bar_signal(
    idx: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    rsi: np.ndarray,
    ema20: np.ndarray,
    ema50: np.ndarray,
    ema200: np.ndarray,
    macd_line: np.ndarray,
    signal_line: np.ndarray,
    hist: np.ndarray,
    is_btc_bearish: bool = False,
    min_score: int = 78,
):
    """تقييم إشارة الدخول عند إغلاق الشمعة رقم idx بدون أي نظرة مستقبلية (No Lookahead)."""
    if idx < 70 or is_btc_bearish:
        return None

    signal_close = float(closes[idx])
    if signal_close <= 0:
        return None

    curr_rsi = float(rsi[idx])
    prev_rsi = float(rsi[idx - 1])

    vol_window = volumes[max(0, idx - 20):idx]
    avg_vol_20 = float(np.mean(vol_window)) if len(vol_window) > 0 else float(volumes[idx])
    curr_vol = float(volumes[idx])
    vol_ratio = (curr_vol / avg_vol_20) if avg_vol_20 > 0 else 1.0

    candle_body = abs(signal_close - float(opens[idx]))
    is_green = signal_close > float(opens[idx])
    lower_wick = min(float(opens[idx]), signal_close) - float(lows[idx])
    has_rejection_wick = lower_wick > (candle_body * 0.9)

    setup_type = ""
    setup_name = ""
    score = 0
    stop_loss = 0.0
    sl_min = 4.5

    # 1. OVERSOLD_BOUNCE
    if (
        float(np.min(rsi[idx - 3:idx])) < 29.5
        and curr_rsi >= 31.0
        and curr_rsi > prev_rsi + 1.5
        and is_green
        and (vol_ratio >= 1.35 or has_rejection_wick)
        and hist[idx] > hist[idx - 1]
        and signal_close >= ema50[idx] * 0.82
    ):
        score = 84
        setup_type = "OVERSOLD_BOUNCE"
        setup_name = "اقتناص ارتداد قاع مؤسسي (Oversold Dip)"
        if vol_ratio >= 1.5:
            score += 6
        if has_rejection_wick:
            score += 5
        recent_floor = float(np.min(lows[max(0, idx - 6):idx + 1]))
        stop_loss = min(signal_close * (1.0 - sl_min / 100.0), recent_floor * 0.985)

    # 2. TREND_PULLBACK
    elif (
        signal_close > ema50[idx] > ema200[idx]
        and 35 <= prev_rsi <= 45
        and curr_rsi > prev_rsi + 2.0
        and is_green
        and signal_close > closes[idx - 1]
        and hist[idx] > hist[idx - 1]
        and (lows[idx] <= ema50[idx] * 1.015 or has_rejection_wick)
    ):
        score = 84
        setup_type = "TREND_PULLBACK"
        setup_name = "إعادة اختبار ترند صاعد (Golden Pullback)"
        if signal_close > ema200[idx]:
            score += 6
        if has_rejection_wick or vol_ratio >= 1.2:
            score += 5
        swing_support = float(np.min(lows[max(0, idx - 10):idx + 1])) * 0.988
        stop_loss = min(signal_close * (1.0 - sl_min / 100.0), swing_support)

    # 3. BULLISH_DIVERGENCE
    elif True:
        sub_lows = lows[idx - 20:idx - 3]
        sub_rsi = rsi[idx - 20:idx - 3]
        sub_hist = hist[idx - 20:idx - 3]
        if len(sub_lows) > 0:
            min_idx = int(np.argmin(sub_lows))
            prev_swing_low = float(sub_lows[min_idx])
            prev_swing_rsi = float(sub_rsi[min_idx])
            prev_swing_hist = float(sub_hist[min_idx])
            curr_low = float(np.min(lows[idx - 2:idx + 1]))

            if (
                prev_swing_low * 0.992 <= curr_low <= prev_swing_low * 1.012
                and prev_swing_rsi <= 36.0
                and curr_rsi >= prev_swing_rsi + 3.5
                and curr_rsi > prev_rsi + 2.0
                and hist[idx] > prev_swing_hist
                and hist[idx] > hist[idx - 1]
                and is_green
                and signal_close > max(float(closes[idx - 1]), float(opens[idx - 1]))
                and (has_rejection_wick or vol_ratio >= 1.25)
                and signal_close >= ema50[idx] * 0.90
            ):
                score = 83
                setup_type = "BULLISH_DIVERGENCE"
                setup_name = "انفراج إيجابي للزخم (Bullish Divergence)"
                if vol_ratio >= 1.35:
                    score += 5
                if has_rejection_wick:
                    score += 5
                stop_loss = min(signal_close * (1.0 - sl_min / 100.0), curr_low * 0.985)

    # 4. VOLUME_BREAKOUT
    if score == 0:
        if (
            vol_ratio >= 1.50
            and 30 <= prev_rsi <= 38
            and curr_rsi > prev_rsi + 2.5
            and is_green
            and signal_close > closes[idx - 1]
            and hist[idx] > hist[idx - 1]
            and signal_close >= ema50[idx] * 0.86
        ):
            score = 82
            setup_type = "VOLUME_BREAKOUT"
            setup_name = "اختراق وزخم سيولة شرائية (Volume Breakout)"
            if vol_ratio >= 2.0:
                score += 6
            if signal_close > opens[idx - 1]:
                score += 4
            recent_floor = float(np.min(lows[max(0, idx - 6):idx + 1]))
            stop_loss = min(signal_close * (1.0 - sl_min / 100.0), recent_floor * 0.985)

    if score < min_score:
        return None

    sl_dist = signal_close - stop_loss
    sl_pct = (sl_dist / signal_close) * 100.0
    if sl_pct < sl_min:
        stop_loss = signal_close * (1.0 - sl_min / 100.0)
        sl_dist = signal_close - stop_loss
        sl_pct = sl_min
    elif sl_pct > 7.5:
        return None

    tp1 = signal_close + (sl_dist * 0.90)
    tp2 = signal_close + (sl_dist * 2.15)
    rr_ratio = round((tp2 - signal_close) / sl_dist, 2)
    if rr_ratio < 1.85:
        return None

    return {
        "setup_type": setup_type,
        "setup_name": setup_name,
        "score": min(score, 98),
        "entry": float(signal_close),
        "sl": float(stop_loss),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "sl_dist": float(sl_dist),
        "rr_ratio": rr_ratio,
    }


def simulate_trade_on_bars(signal: dict, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, start_idx: int, max_hold_bars: int = 36) -> dict:
    """
    محاكاة صفقة حقيقية شمعة بشمعة بعد الدخول عند إغلاق شمعة الإشارة:
    - 50% تُباع عند TP1، و50% تستمر مع Breakeven (+0.25%) و Trailing Stop (1.0 R) حتى TP2.
    - تشمل خصم رسوم التداول الفوري (0.2% إجمالي ذهاب وعودة).
    """
    entry = signal["entry"]
    initial_sl = signal["sl"]
    current_sl = initial_sl
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    r_dist = max(entry - initial_sl, entry * 0.025)

    tp1_hit = False
    highest = entry
    stage = "INITIAL"
    half1_return_pct = 0.0
    half2_return_pct = 0.0
    exit_idx = min(start_idx + max_hold_bars, len(closes) - 1)
    exit_reason = "TIME_EXIT"

    round_trip_fee_pct = SPOT_FEE_RATE * 2 * 100.0  # 0.20%

    for j in range(start_idx + 1, min(start_idx + max_hold_bars + 1, len(closes))):
        bar_low = float(lows[j])
        bar_high = float(highs[j])

        # 1. فحص ضرب وقف الخسارة أولاً (معيار محافظ يمنع المبالغة في الأرباح)
        if bar_low <= current_sl:
            exit_idx = j
            if not tp1_hit:
                loss_pct = ((current_sl - entry) / entry) * 100.0 - round_trip_fee_pct
                return {
                    "net_pct": round(loss_pct, 3),
                    "r_multiple": round(loss_pct / ((r_dist / entry) * 100.0), 2),
                    "outcome": "LOSS" if loss_pct < 0 else "WIN",
                    "exit_reason": "STOP_LOSS",
                    "bars_held": j - start_idx,
                    "tp1_hit": False,
                    "tp2_hit": False,
                }
            else:
                half2_return_pct = ((current_sl - entry) / entry) * 100.0 - round_trip_fee_pct
                total_pct = 0.5 * half1_return_pct + 0.5 * half2_return_pct
                return {
                    "net_pct": round(total_pct, 3),
                    "r_multiple": round(total_pct / ((r_dist / entry) * 100.0), 2),
                    "outcome": "WIN" if total_pct > 0 else "LOSS",
                    "exit_reason": f"PROTECTED_{stage}",
                    "bars_held": j - start_idx,
                    "tp1_hit": True,
                    "tp2_hit": False,
                }

        # 2. فحص وصول السعر إلى الهدف الأول TP1
        if not tp1_hit and bar_high >= tp1:
            tp1_hit = True
            half1_return_pct = ((tp1 - entry) / entry) * 100.0 - round_trip_fee_pct
            # رفع وقف الخسارة فوراً إلى نقطة التعادل + عمولة المنصة (+0.25%)
            be_price = entry * 1.0025
            current_sl = max(current_sl, be_price)
            stage = "BREAKEVEN"

        # 3. فحص وصول السعر إلى الهدف الثاني TP2
        if tp1_hit and bar_high >= tp2:
            half2_return_pct = ((tp2 - entry) / entry) * 100.0 - round_trip_fee_pct
            total_pct = 0.5 * half1_return_pct + 0.5 * half2_return_pct
            return {
                "net_pct": round(total_pct, 3),
                "r_multiple": round(total_pct / ((r_dist / entry) * 100.0), 2),
                "outcome": "WIN",
                "exit_reason": "TP2_FULL_TARGET",
                "bars_held": j - start_idx,
                "tp1_hit": True,
                "tp2_hit": True,
            }

        # 4. تحديث القمة والوقف المتحرك (Lock TP1 + Trailing Stop 1.0R) بعد تحقق TP1
        if bar_high > highest:
            highest = bar_high
        if tp1_hit:
            mid_tp1_tp2 = tp1 + 0.5 * (tp2 - tp1)
            if highest >= mid_tp1_tp2 and tp1 > current_sl:
                current_sl = tp1
                stage = "LOCK_TP1"
            trail_sl = highest - (1.0 * r_dist)
            if trail_sl > current_sl:
                current_sl = trail_sl
                stage = "TRAILING"

    # انتهاء مدة الاحتفاظ القصوى دون ضرب SL أو TP2
    last_close = float(closes[exit_idx])
    if tp1_hit:
        half2_return_pct = ((last_close - entry) / entry) * 100.0 - round_trip_fee_pct
        total_pct = 0.5 * half1_return_pct + 0.5 * half2_return_pct
    else:
        total_pct = ((last_close - entry) / entry) * 100.0 - round_trip_fee_pct

    return {
        "net_pct": round(total_pct, 3),
        "r_multiple": round(total_pct / ((r_dist / entry) * 100.0), 2),
        "outcome": "WIN" if total_pct > 0 else "LOSS",
        "exit_reason": exit_reason,
        "bars_held": exit_idx - start_idx,
        "tp1_hit": tp1_hit,
        "tp2_hit": False,
    }


def backtest_symbol(symbol: str, interval: str = "240", limit: int = 500, btc_bearish_map: dict = None) -> list:
    klines = fetch_historical_klines(symbol, interval=interval, limit=limit)
    if not klines or len(klines) < 80:
        return []

    timestamps = [int(k[0]) for k in klines]
    opens = np.array([float(k[1]) for k in klines])
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    volumes = np.array([float(k[5]) for k in klines])

    rsi = calculate_rsi(closes, 14)
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, min(len(closes), 200))
    macd_line, signal_line, hist = calculate_macd(closes)

    trades = []
    cooldown_until = 0

    for idx in range(60, len(closes) - 4):
        if idx < cooldown_until:
            continue
        ts = timestamps[idx]
        is_bear = bool(btc_bearish_map.get(ts, False)) if btc_bearish_map else False
        sig = evaluate_bar_signal(
            idx, opens, highs, lows, closes, volumes,
            rsi, ema20, ema50, ema200, macd_line, signal_line, hist,
            is_btc_bearish=is_bear
        )
        if not sig:
            continue

        sim = simulate_trade_on_bars(sig, highs, lows, closes, start_idx=idx, max_hold_bars=36)
        cooldown_until = idx + max(sim["bars_held"], 3)

        trades.append({
            "symbol": symbol,
            "timestamp": datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M"),
            "setup_type": sig["setup_type"],
            "setup_name": sig["setup_name"],
            "score": sig["score"],
            "entry": round(sig["entry"], 6),
            "sl": round(sig["sl"], 6),
            "tp1": round(sig["tp1"], 6),
            "tp2": round(sig["tp2"], 6),
            **sim,
        })

    return trades


def build_btc_regime_map(interval: str = "240", limit: int = 500) -> dict:
    """بناء خريطة حالة الانهيار الحاد للبيتكوين لكل شمعة 4 ساعات لمنع الانحياز المستقبلي."""
    klines = fetch_historical_klines("BTCUSDT", interval=interval, limit=limit)
    if not klines or len(klines) < 60:
        return {}
    ts_list = [int(k[0]) for k in klines]
    closes = np.array([float(k[4]) for k in klines])
    rsi = calculate_rsi(closes, 14)
    regime_map = {}
    for i in range(50, len(closes)):
        chg_3bars = ((closes[i] - closes[max(0, i - 3)]) / closes[max(0, i - 3)]) * 100.0
        # حجب الدخول فقط أثناء الانهيارات الحادة للبيتكوين (> -4.5% خلال 12 ساعة مع RSI < 28)
        is_crash = bool(chg_3bars < -4.5 and rsi[i] < 28)
        regime_map[ts_list[i]] = is_crash
    return regime_map


DEFAULT_BACKTEST_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
    "DOTUSDT", "NEARUSDT", "APTUSDT", "LTCUSDT", "UNIUSDT",
    "PEPEUSDT", "SHIBUSDT", "RENDERUSDT", "FETUSDT", "INJUSDT",
    "ARBUSDT", "OPUSDT", "TAOUSDT", "WIFUSDT", "ONDOUSDT",
]


def run_portfolio_backtest(symbols: list = None, interval: str = "240", limit: int = 500, position_size_pct: float = 20.0) -> dict:
    """
    تشغيل اختبار تاريخي شامل عبر سلة العملات وحساب:
    - نسبة النجاح (Win Rate %)
    - معامل الربح (Profit Factor)
    - متوسط العائد لكل مخاطرة (Avg R-Multiple)
    - أقصى تراجع للمحفظة (Max Drawdown %)
    - تفصيل الأداء لكل نموذج فني (Per-Setup Breakdown)
    """
    target_symbols = symbols or DEFAULT_BACKTEST_SYMBOLS
    t0 = time.time()
    btc_map = build_btc_regime_map(interval=interval, limit=limit)

    all_trades = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(backtest_symbol, s, interval, limit, btc_map): s for s in target_symbols}
        for f in as_completed(futs):
            try:
                res = f.result()
                if res:
                    all_trades.extend(res)
            except Exception:
                pass

    all_trades.sort(key=lambda x: x["timestamp"])

    total = len(all_trades)
    if total == 0:
        return {
            "retCode": 0,
            "totalTrades": 0,
            "message": "لم يتم العثور على صفقات مطابقة في الفترة المحددة.",
        }

    wins = [t for t in all_trades if t["net_pct"] > 0]
    losses = [t for t in all_trades if t["net_pct"] <= 0]
    win_rate = round((len(wins) / total) * 100.0, 2)
    tp1_rate = round((sum(1 for t in all_trades if t["tp1_hit"]) / total) * 100.0, 2)
    tp2_rate = round((sum(1 for t in all_trades if t["tp2_hit"]) / total) * 100.0, 2)

    gross_profit = sum(t["net_pct"] for t in wins)
    gross_loss = abs(sum(t["net_pct"] for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0

    avg_win_pct = round(float(np.mean([t["net_pct"] for t in wins])), 2) if wins else 0.0
    avg_loss_pct = round(float(np.mean([t["net_pct"] for t in losses])), 2) if losses else 0.0
    avg_r = round(float(np.mean([t["r_multiple"] for t in all_trades])), 2)

    # محاكاة منحنى السيولة (Equity Curve) مع تخصيص position_size_pct% من المحفظة لكل صفقة
    equity = 1000.0
    peak_equity = 1000.0
    max_dd_pct = 0.0
    alloc_frac = max(0.05, min(position_size_pct / 100.0, 1.0))

    for t in all_trades:
        trade_pnl_usd = equity * alloc_frac * (t["net_pct"] / 100.0)
        equity += trade_pnl_usd
        if equity > peak_equity:
            peak_equity = equity
        dd = ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
        if dd > max_dd_pct:
            max_dd_pct = dd

    total_portfolio_return_pct = round(((equity - 1000.0) / 1000.0) * 100.0, 2)

    # تفصيل الأداء حسب النموذج الفني
    by_setup = {}
    for st in ["TREND_PULLBACK", "VOLUME_BREAKOUT", "BULLISH_DIVERGENCE", "OVERSOLD_BOUNCE"]:
        sub = [t for t in all_trades if t["setup_type"] == st]
        if not sub:
            continue
        sw = [t for t in sub if t["net_pct"] > 0]
        sl_list = [t for t in sub if t["net_pct"] <= 0]
        gp = sum(t["net_pct"] for t in sw)
        gl = abs(sum(t["net_pct"] for t in sl_list))
        by_setup[st] = {
            "trades": len(sub),
            "wins": len(sw),
            "losses": len(sl_list),
            "winRatePct": round((len(sw) / len(sub)) * 100.0, 2),
            "tp1HitRatePct": round((sum(1 for t in sub if t["tp1_hit"]) / len(sub)) * 100.0, 2),
            "tp2HitRatePct": round((sum(1 for t in sub if t["tp2_hit"]) / len(sub)) * 100.0, 2),
            "profitFactor": round(gp / gl, 2) if gl > 0 else 99.0,
            "avgNetPct": round(float(np.mean([t["net_pct"] for t in sub])), 2),
            "avgR": round(float(np.mean([t["r_multiple"] for t in sub])), 2),
            "sumNetPct": round(sum(t["net_pct"] for t in sub), 2),
        }

    elapsed = round(time.time() - t0, 2)
    return {
        "retCode": 0,
        "testedSymbolsCount": len(target_symbols),
        "interval": interval,
        "candlesPerSymbol": limit,
        "elapsedSec": elapsed,
        "summary": {
            "totalTrades": total,
            "winningTrades": len(wins),
            "losingTrades": len(losses),
            "winRatePct": win_rate,
            "tp1HitRatePct": tp1_rate,
            "tp2HitRatePct": tp2_rate,
            "profitFactor": profit_factor,
            "avgWinPct": avg_win_pct,
            "avgLossPct": avg_loss_pct,
            "avgRMultiple": avg_r,
            "startingBalanceUsdt": 1000.0,
            "endingBalanceUsdt": round(equity, 2),
            "portfolioReturnPct": total_portfolio_return_pct,
            "maxDrawdownPct": round(max_dd_pct, 2),
        },
        "bySetup": by_setup,
        "recentTrades": all_trades[-25:][::-1],
    }


if __name__ == "__main__":
    if sys.platform.startswith("win") and sys.stdout:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("⏳ جاري تشغيل الاختبار التاريخي (Backtest) على بيانات Bybit الحقيقية (فريم 4 ساعات)...")
    report = run_portfolio_backtest(limit=500)
    print(json.dumps(report, ensure_ascii=False, indent=2))
