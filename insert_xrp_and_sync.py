import json
import time
import os
import sys
from datetime import datetime

WEB_DIR = r"c:\wark_monasba\antygrafty\jood\bybit_screener_web"
sys.path.append(WEB_DIR)

from server import load_real_positions, save_real_positions, sync_real_positions_with_bybit, bybit_signed_request

# 1. Load existing
acc = load_real_positions()
open_pos = acc.get("openPositions", [])
history = acc.get("history", [])

existing_symbols = {p["symbol"] for p in open_pos}

# 2. Add XRPUSDT if not present
if "XRPUSDT" not in existing_symbols:
    xrp_pos = {
        "id": "REAL-1790375492-XRPU",
        "symbol": "XRPUSDT",
        "baseCoin": "XRP",
        "side": "Buy",
        "orderType": "Market",
        "entryPrice": 1.5652,
        "currentPrice": 1.5508,
        "qty": 63.81696,
        "amountUsdt": 100.0,
        "feePaid": 0.1,
        "currentValue": 98.97,
        "unrealizedPnL": -1.03,
        "unrealizedPnLPct": -1.03,
        "tp1": 1.6266,
        "tp2": 1.6969,
        "tp3": 1.7686,
        "sl": 1.5026,
        "setupName": "📈 إعادة اختبار ترند صاعد (Golden Pullback)",
        "openedAt": "2026-09-26 01:31:32",
        "status": "OPEN",
        "orderId": "2312231651549473536",
        "tpOrderId": "2312231662286891776, 2312231663838784256"
    }
    open_pos.insert(0, xrp_pos)
    print("Added XRPUSDT to open positions.")

# 3. Add SLXUSDT if not present
if "SLXUSDT" not in existing_symbols:
    slx_pos = {
        "id": "REAL-1790375554-SLXU",
        "symbol": "SLXUSDT",
        "baseCoin": "SLX",
        "side": "Buy",
        "orderType": "Market",
        "entryPrice": 0.0699,
        "currentPrice": 0.0694,
        "qty": 715.3,
        "amountUsdt": 50.0,
        "feePaid": 0.05,
        "currentValue": 49.64,
        "unrealizedPnL": -0.36,
        "unrealizedPnLPct": -0.72,
        "tp1": 0.073395,
        "tp2": 0.07689,
        "tp3": 0.080385,
        "sl": 0.066754,
        "setupName": "📉 انفراج إيجابي للزخم (Bullish Divergence)",
        "openedAt": "2026-09-26 01:32:34",
        "status": "OPEN",
        "orderId": "2312232174352689920",
        "tpOrderId": "2312232185215937280, 2312232186801384192"
    }
    open_pos.insert(0, slx_pos)
    print("Added SLXUSDT to open positions.")

# 4. Add CRVUSDT if not present
if "CRVUSDT" not in existing_symbols:
    crv_pos = {
        "id": "REAL-1790403575-CRVU",
        "symbol": "CRVUSDT",
        "baseCoin": "CRV",
        "side": "Buy",
        "orderType": "Market",
        "entryPrice": 0.35,
        "currentPrice": 0.35,
        "qty": 142.8,
        "amountUsdt": 49.98,
        "feePaid": 0.05,
        "currentValue": 49.98,
        "unrealizedPnL": 0.0,
        "unrealizedPnLPct": 0.0,
        "tp1": 0.3640,
        "tp2": 0.3798,
        "tp3": 0.4050,
        "sl": 0.3360,
        "setupName": "💎 اقتناص ارتداد قاع (Oversold Dip)",
        "openedAt": "2026-09-26 09:19:35",
        "status": "OPEN",
        "orderId": "2312299863834195456",
        "tpOrderId": "2312299876500993536, 2312299881181836800"
    }
    open_pos.insert(0, crv_pos)
    print("Added CRVUSDT to open positions.")

acc["openPositions"] = open_pos
save_real_positions(acc)

# 5. Now run full sync with Bybit executions
print("Syncing with Bybit live executions...")
sync_real_positions_with_bybit()

updated_acc = load_real_positions()
print("\n--- Current Open Positions in real_positions.json ---")
for p in updated_acc.get("openPositions", []):
    print(f"  {p['symbol']}: Qty={p['qty']}, Entry={p['entryPrice']}, Amount=${p['amountUsdt']}")

print(f"\nTotal Open Positions: {len(updated_acc.get('openPositions', []))}")
print(f"Total History Items: {len(updated_acc.get('history', []))}")
