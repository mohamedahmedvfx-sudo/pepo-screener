#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
🛡️ Bot Protection Engine (محرك حماية رأس المال)
------------------------------------------------------------------------------
1. Secrets loading      : env vars -> secrets.json (no hardcoded tokens).
2. Protective stop      : breakeven (+fees) after TP1, lock TP1, trailing stop.
3. Daily loss breaker   : blocks new buys once the daily loss limit is hit.
4. Error alerting       : throttled Telegram alerts + guard heartbeat watchdog.
==============================================================================
"""

import os
import json
import time
import threading
import traceback
from datetime import datetime

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(WEB_DIR, "bot_settings.json")
STATE_FILE = os.path.join(WEB_DIR, "bot_state.json")
SECRETS_FILE = os.path.join(WEB_DIR, "secrets.json")
ENV_FILE = os.path.join(WEB_DIR, ".env")

DEFAULT_SETTINGS = {
    # --- Protective stop ---
    "breakevenBufferPct": 0.25,       # SL after TP1 = entry * (1 + 0.25%) to cover fees
    "lockTp1AtMidwayToTp2": True,     # once price reaches midway TP1->TP2, SL = TP1
    "trailingEnabled": True,          # trail the remaining 50% after TP1
    "trailDistanceR": 1.0,            # trailing distance in R (R = entry - original SL)
    "defaultStopLossPct": 5.0,        # used when a buy is placed without any SL
    # --- Daily loss circuit breaker ---
    "dailyLossLimitEnabled": True,
    "dailyMaxLossPct": 3.0,           # % of account equity at day start
    "dailyMaxLossUsdt": 0,            # if > 0, overrides the % rule
    "maxOpenPositions": 4,            # max simultaneous open positions
    # --- Smart Auto-Trader (بند 4: التداول الآلي الذكي بضوابط صارمة) ---
    "autoTradeEnabled": False,        # OFF by default until explicitly enabled by user
    "autoTradeMode": "demo",          # 'demo' (محاكاة آمنة) or 'live' (حساب حقيقي)
    "autoTradeMinScore": 86,          # minimum setup score (86+ Elite only)
    "autoTradeAmountUsdt": 15.0,      # USDT allocated per auto-trade
    "autoTradeMaxPerDay": 3,          # max auto-trades executed per day
    "autoTradeCooldownHours": 12,     # cooldown hours per symbol before re-entering
    "autoTradeRequireSafeMarket": True,  # block auto-buys when BTC is BEARISH
    "autoTradeIntervalSec": 300,      # scan interval for auto-trader (5 minutes)
    # --- Alerts ---
    "errorAlertCooldownSec": 600,     # same error is reported at most once per 10 min
    "guardStaleAlertSec": 90,         # alert if the guard loop stalls longer than this
}

_LOCK = threading.RLock()


# ==============================================================================
# 🔐 Secrets (.env + environment variables + secrets.json fallback)
# ==============================================================================
def load_dotenv_file(env_path=ENV_FILE):
    """Load key=value pairs from .env into os.environ (without overriding existing OS env)."""
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ and v:
                    os.environ[k] = v
    except Exception as e:
        print(f"⚠️ [Secrets] Could not load .env: {e}")


def save_env_vars(updates, env_path=ENV_FILE):
    """Safely update key=value pairs in .env and refresh os.environ in memory."""
    with _LOCK:
        existing = {}
        order = []
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            if k not in existing:
                                order.append(k)
                            existing[k] = v.strip().strip('"').strip("'")
            except Exception:
                pass
        for k, v in updates.items():
            k_clean = str(k).strip()
            v_clean = str(v).strip()
            if k_clean not in existing:
                order.append(k_clean)
            existing[k_clean] = v_clean
            os.environ[k_clean] = v_clean
        try:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("# Bybit Spot AI Screener Pro - Local Secrets (NEVER COMMIT TO GIT)\n")
                for k in order:
                    f.write(f"{k}={existing[k]}\n")
        except Exception as e:
            print(f"⚠️ [Secrets] Could not save .env: {e}")


load_dotenv_file()


def get_secret(name, default=""):
    """Read a secret from environment variables first, then from secrets.json."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    try:
        if os.path.exists(SECRETS_FILE):
            with open(SECRETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            v = data.get(name)
            if v:
                return str(v).strip()
    except Exception as e:
        print(f"⚠️ [Secrets] Could not read secrets.json: {e}")
    return default


# ==============================================================================
# ⚙️ Settings & persistent state
# ==============================================================================
def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k, v in user.items():
                    if k in DEFAULT_SETTINGS:
                        settings[k] = v
        else:
            save_settings(settings)
    except Exception as e:
        print(f"⚠️ [Settings] Using defaults, read error: {e}")
    return settings


def save_settings(settings):
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in (settings or {}).items() if k in DEFAULT_SETTINGS})
    with _LOCK:
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ [Settings] Save error: {e}")
    return merged


def _load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_state(state):
    with _LOCK:
        try:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            print(f"⚠️ [State] Save error: {e}")


# ==============================================================================
# 📣 Throttled error alerts + guard heartbeat
# ==============================================================================
_ALERT_FN = None
_LAST_ALERT_AT = {}
_GUARD_LAST_OK = time.time()
_GUARD_STALE_NOTIFIED = False


def set_alert_function(fn):
    """fn(text) -> sends a Telegram message asynchronously."""
    global _ALERT_FN
    _ALERT_FN = fn


def report_error(key, message, exc=None, force=False):
    """Print every error, but send the same error key to Telegram at most once per cooldown."""
    detail = ""
    if exc is not None:
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    print(f"⚠️ [Guard Error:{key}] {message} {detail}")
    cooldown = float(load_settings().get("errorAlertCooldownSec", 600))
    now = time.time()
    with _LOCK:
        last = _LAST_ALERT_AT.get(key, 0)
        if not force and now - last < cooldown:
            return
        _LAST_ALERT_AT[key] = now
    if _ALERT_FN:
        text = f"⚠️ <b>تحذير من حارس الحماية</b>\n\n{message}"
        if detail:
            text += f"\n\n<code>{detail[:300]}</code>"
        try:
            _ALERT_FN(text)
        except Exception:
            pass


def mark_guard_ok():
    global _GUARD_LAST_OK, _GUARD_STALE_NOTIFIED
    _GUARD_LAST_OK = time.time()
    if _GUARD_STALE_NOTIFIED:
        _GUARD_STALE_NOTIFIED = False
        if _ALERT_FN:
            _ALERT_FN("✅ <b>حارس الحماية عاد للعمل بشكل طبيعي.</b>")


def guard_watchdog_loop():
    """Alerts when the guard loop has not completed a healthy cycle for too long."""
    global _GUARD_STALE_NOTIFIED
    while True:
        time.sleep(15)
        try:
            limit = float(load_settings().get("guardStaleAlertSec", 90))
            stale = time.time() - _GUARD_LAST_OK
            if stale > limit and not _GUARD_STALE_NOTIFIED:
                _GUARD_STALE_NOTIFIED = True
                if _ALERT_FN:
                    _ALERT_FN(
                        "🚨 <b>حارس وقف الخسارة متوقف!</b>\n\n"
                        f"لم يكمل الحارس أي دورة فحص ناجحة منذ {int(stale)} ثانية.\n"
                        "الصفقات المفتوحة قد تكون بدون حماية — راجع الاتصال بـ Bybit أو السيرفر فوراً."
                    )
        except Exception:
            pass


def guard_status():
    return {
        "lastOkSecondsAgo": round(time.time() - _GUARD_LAST_OK, 1),
        "staleAlertActive": _GUARD_STALE_NOTIFIED,
    }


# ==============================================================================
# 🎯 Protective stop logic (pure function — easy to test)
# ==============================================================================
def breakeven_price(entry, settings=None):
    settings = settings or load_settings()
    buf = float(settings.get("breakevenBufferPct", 0.25))
    return round(float(entry) * (1 + buf / 100.0), 8)


def compute_protective_stop(pos, live_price, settings=None):
    """
    Returns dict: {sl, stage, highest, originalSl, changed, reason}
    Rules (the stop is NEVER lowered):
      - Before TP1           : keep the original stop.
      - At/After TP1         : stop >= entry + fee buffer (BREAKEVEN).
      - Midway TP1 -> TP2    : stop >= TP1 (LOCK_TP1).
      - Trailing after TP1   : stop >= highest - trailDistanceR * R (TRAILING).
    """
    settings = settings or load_settings()
    entry = float(pos.get("entryPrice") or 0)
    sl = float(pos.get("sl") or 0)
    stage = pos.get("protectionStage") or "INITIAL"
    original_sl = float(pos.get("originalSl") or 0) or sl
    highest = max(float(pos.get("highestPrice") or 0), float(live_price or 0), entry)

    result = {
        "sl": sl,
        "stage": stage,
        "highest": highest,
        "originalSl": original_sl,
        "changed": False,
        "reason": STAGE_LABELS.get(stage, stage),
    }
    if entry <= 0 or not live_price or live_price <= 0:
        return result

    risk = entry - original_sl if 0 < original_sl < entry else entry * 0.04
    new_sl = sl
    new_stage = stage
    tp1 = float(pos.get("tp1") or 0)
    tp2 = float(pos.get("tp2") or 0)

    tp1_reached = bool(pos.get("tp1Executed")) or (tp1 > entry and highest >= tp1)
    if tp1_reached:
        be = breakeven_price(entry, settings)
        if new_sl < be:
            new_sl, new_stage = be, "BREAKEVEN"

        if settings.get("lockTp1AtMidwayToTp2", True) and entry < tp1 < tp2:
            if highest >= tp1 + 0.5 * (tp2 - tp1) and new_sl < tp1:
                new_sl, new_stage = tp1, "LOCK_TP1"

        if settings.get("trailingEnabled", True):
            trail = highest - float(settings.get("trailDistanceR", 1.0)) * risk
            if trail > new_sl:
                new_sl, new_stage = trail, "TRAILING"

    if new_sl > sl * 1.0000001:
        result["sl"] = round(new_sl, 8)
        result["stage"] = new_stage
        result["changed"] = True
        result["reason"] = STAGE_LABELS.get(new_stage, new_stage)
    return result


STAGE_LABELS = {
    "INITIAL": "وقف الخسارة الأساسي",
    "BREAKEVEN": "تأمين الدخول (Breakeven + الرسوم)",
    "LOCK_TP1": "تأمين ربح الهدف الأول",
    "TRAILING": "وقف متحرك يلاحق السعر",
}


# ==============================================================================
# 🧯 Daily loss circuit breaker
# ==============================================================================
_LAST_DAILY_CHECK = 0


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def realized_pnl_today(history):
    today = _today()
    total = 0.0
    for h in history or []:
        if str(h.get("closedAt", "")).startswith(today):
            try:
                total += float(h.get("realizedPnL", 0) or 0)
            except (TypeError, ValueError):
                pass
    return round(total, 2)


def check_daily_loss(history, equity_fn=None, force=False):
    """
    Evaluates today's realized PnL against the limit. Throttled to every 30s.
    equity_fn() -> current account equity in USDT (0 if unknown).
    Returns the daily state dict.
    """
    global _LAST_DAILY_CHECK
    settings = load_settings()
    state = _load_state()
    today = _today()
    daily = state.get("daily") or {}

    if daily.get("day") != today:
        daily = {"day": today, "startEquity": 0.0, "locked": False, "lockedAt": None, "manualUnlock": False, "reason": ""}
        state["daily"] = daily
        _save_state(state)

    now = time.time()
    if not force and now - _LAST_DAILY_CHECK < 30:
        return daily
    _LAST_DAILY_CHECK = now

    if not settings.get("dailyLossLimitEnabled", True):
        return daily

    if not daily.get("startEquity") and equity_fn is not None:
        try:
            eq = float(equity_fn() or 0)
        except Exception:
            eq = 0.0
        if eq > 0:
            daily["startEquity"] = round(eq, 2)

    limit_usdt = float(settings.get("dailyMaxLossUsdt", 0) or 0)
    if limit_usdt <= 0 and daily.get("startEquity"):
        limit_usdt = round(daily["startEquity"] * float(settings.get("dailyMaxLossPct", 3.0)) / 100.0, 2)

    pnl = realized_pnl_today(history)
    daily["realizedToday"] = pnl
    daily["limitUsdt"] = limit_usdt

    if limit_usdt > 0 and pnl <= -limit_usdt and not daily.get("locked") and not daily.get("manualUnlock"):
        daily["locked"] = True
        daily["lockedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        daily["reason"] = f"تم تجاوز حد الخسارة اليومي ({pnl:,.2f} USDT من أصل -{limit_usdt:,.2f} USDT)"
        if _ALERT_FN:
            _ALERT_FN(
                "🧯 <b>تم تفعيل حد الخسارة اليومي!</b>\n\n"
                f"📉 <b>الخسارة المحققة اليوم:</b> {pnl:,.2f} USDT\n"
                f"🛑 <b>الحد المسموح:</b> -{limit_usdt:,.2f} USDT\n\n"
                "⛔ تم إيقاف فتح أي صفقات شراء جديدة حتى بداية اليوم التالي.\n"
                "🛡️ الصفقات المفتوحة ما زالت تحت حماية الحارس."
            )

    state["daily"] = daily
    _save_state(state)
    return daily


def is_trading_locked():
    daily = (_load_state().get("daily") or {})
    return bool(daily.get("day") == _today() and daily.get("locked"))


def reset_daily_lock():
    state = _load_state()
    daily = state.get("daily") or {"day": _today()}
    daily["locked"] = False
    daily["manualUnlock"] = True
    state["daily"] = daily
    _save_state(state)
    return daily


def can_auto_trade_symbol(symbol: str, settings=None) -> tuple:
    """
    Checks if the Smart Auto-Trader is allowed to open a new trade on `symbol` right now.
    Returns (allowed: bool, reason: str).
    """
    settings = settings or load_settings()
    if is_trading_locked():
        return False, "قاطع الخسارة اليومية مفعّل"

    state = _load_state()
    today = _today()
    auto_state = state.get("autoTrader") or {}
    if auto_state.get("day") != today:
        auto_state = {
            "day": today,
            "tradesToday": 0,
            "symbolTimestamps": auto_state.get("symbolTimestamps") or {},
            "recentExecutions": auto_state.get("recentExecutions") or [],
            "lastRunAt": auto_state.get("lastRunAt"),
            "lastStatus": auto_state.get("lastStatus", "جاهز"),
        }
        state["autoTrader"] = auto_state
        _save_state(state)

    max_per_day = int(settings.get("autoTradeMaxPerDay", 3) or 3)
    if int(auto_state.get("tradesToday", 0)) >= max_per_day:
        return False, f"تم الوصول للحد الأقصى للصفقات الآلية اليوم ({max_per_day})"

    cooldown_h = float(settings.get("autoTradeCooldownHours", 12) or 12)
    sym_ts = (auto_state.get("symbolTimestamps") or {}).get(symbol, 0)
    if sym_ts and (time.time() - float(sym_ts)) < cooldown_h * 3600:
        rem_h = round((cooldown_h * 3600 - (time.time() - float(sym_ts))) / 3600, 1)
        return False, f"العملة {symbol} في فترة تهدئة ({rem_h} ساعة متبقية)"

    return True, "مسموح"


def record_auto_trade_execution(symbol: str, mode: str, entry_price: float, amount_usdt: float, score: int, setup_name: str):
    state = _load_state()
    today = _today()
    auto_state = state.get("autoTrader") or {}
    if auto_state.get("day") != today:
        auto_state = {"day": today, "tradesToday": 0, "symbolTimestamps": {}, "recentExecutions": []}

    auto_state["tradesToday"] = int(auto_state.get("tradesToday", 0)) + 1
    sym_map = auto_state.get("symbolTimestamps") or {}
    sym_map[symbol] = time.time()
    auto_state["symbolTimestamps"] = sym_map

    rec = auto_state.get("recentExecutions") or []
    rec.insert(0, {
        "symbol": symbol,
        "mode": mode,
        "entryPrice": entry_price,
        "amountUsdt": amount_usdt,
        "score": score,
        "setupName": setup_name,
        "executedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    auto_state["recentExecutions"] = rec[:30]
    auto_state["lastRunAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    auto_state["lastStatus"] = f"تم تنفيذ {symbol} ({mode}) بنجاح ✅"
    state["autoTrader"] = auto_state
    _save_state(state)


def update_auto_trader_heartbeat(status_msg: str):
    state = _load_state()
    today = _today()
    auto_state = state.get("autoTrader") or {"day": today, "tradesToday": 0, "symbolTimestamps": {}, "recentExecutions": []}
    if auto_state.get("day") != today:
        auto_state["day"] = today
        auto_state["tradesToday"] = 0
    auto_state["lastRunAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    auto_state["lastStatus"] = status_msg
    state["autoTrader"] = auto_state
    _save_state(state)


def compute_journal_analytics(history: list) -> dict:
    """
    يحلل سجل الصفقات المغلقة (الحقيقية أو المحاكاة) ويستخرج:
    - معامل الربح (Profit Factor)
    - متوسط الصفقة الرابحة ومتوسط الخاسرة بالدولار والنسبة المئوية
    - التوقع الرياضي للصفقة (Expectancy USDT)
    - أطول سلسلة انتصارات وأطول سلسلة خسائر
    - تفصيل الأداء حسب النموذج الفني (Setup Breakdown)
    """
    if not history:
        return {
            "totalTrades": 0,
            "winRatePct": 0.0,
            "profitFactor": 0.0,
            "expectancyUsdt": 0.0,
            "avgWinUsdt": 0.0,
            "avgLossUsdt": 0.0,
            "bestTradeUsdt": 0.0,
            "worstTradeUsdt": 0.0,
            "maxWinStreak": 0,
            "maxLossStreak": 0,
            "bySetup": {},
        }

    pnls = [float(h.get("realizedPnL", 0) or 0) for h in history]
    wins = [p for p in pnls if p > 0.005]
    losses = [p for p in pnls if p < -0.005]
    total = len(pnls)

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    win_rate = round((len(wins) / total) * 100.0, 1) if total > 0 else 0.0
    avg_win = round(gross_profit / len(wins), 2) if wins else 0.0
    avg_loss = round(-gross_loss / len(losses), 2) if losses else 0.0
    expectancy = round(sum(pnls) / total, 2) if total > 0 else 0.0

    # حساب سلاسل الفوز والخسارة المتتالية (من الأقدم للأحدث)
    max_w_streak = 0
    max_l_streak = 0
    cur_w = 0
    cur_l = 0
    for p in reversed(pnls):
        if p > 0.005:
            cur_w += 1
            cur_l = 0
            if cur_w > max_w_streak:
                max_w_streak = cur_w
        elif p < -0.005:
            cur_l += 1
            cur_w = 0
            if cur_l > max_l_streak:
                max_l_streak = cur_l

    by_setup = {}
    for h in history:
        sname = str(h.get("setupName") or h.get("exitReason") or "صفقة عامة").strip()
        # تبسيط اسم النموذج
        if "Pullback" in sname or "ترند صاعد" in sname:
            key = "إعادة اختبار ترند صاعد (Golden Pullback)"
        elif "Breakout" in sname or "اختراق" in sname:
            key = "اختراق وزخم سيولة (Volume Breakout)"
        elif "Divergence" in sname or "انفراج" in sname:
            key = "انفراج إيجابي (Bullish Divergence)"
        elif "Oversold" in sname or "تشبع" in sname or "قاع" in sname:
            key = "اقتناص ارتداد قاع (Oversold Dip)"
        else:
            key = "صفقة تداول فوري Bybit"

        entry = by_setup.setdefault(key, {"trades": 0, "wins": 0, "losses": 0, "netPnlUsdt": 0.0})
        p = float(h.get("realizedPnL", 0) or 0)
        entry["trades"] += 1
        if p > 0.005:
            entry["wins"] += 1
        elif p < -0.005:
            entry["losses"] += 1
        entry["netPnlUsdt"] = round(entry["netPnlUsdt"] + p, 2)

    for k, v in by_setup.items():
        v["winRatePct"] = round((v["wins"] / v["trades"]) * 100.0, 1) if v["trades"] > 0 else 0.0

    return {
        "totalTrades": total,
        "winRatePct": win_rate,
        "profitFactor": pf,
        "expectancyUsdt": expectancy,
        "avgWinUsdt": avg_win,
        "avgLossUsdt": avg_loss,
        "bestTradeUsdt": round(max(pnls), 2) if pnls else 0.0,
        "worstTradeUsdt": round(min(pnls), 2) if pnls else 0.0,
        "maxWinStreak": max_w_streak,
        "maxLossStreak": max_l_streak,
        "bySetup": by_setup,
    }


def get_status():
    state = _load_state()
    return {
        "settings": load_settings(),
        "daily": state.get("daily") or {},
        "tradingLocked": is_trading_locked(),
        "guard": guard_status(),
        "autoTrader": state.get("autoTrader") or {"day": _today(), "tradesToday": 0, "recentExecutions": [], "lastStatus": "متوقف"},
    }

