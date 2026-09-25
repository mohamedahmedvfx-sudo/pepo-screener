# ⚡ Bybit Spot AI Screener Pro & Autonomous Stop-Loss Engine

A high-performance real-time cryptocurrency spot scanner, autonomous trading terminal, and cloud-native Stop-Loss & Take-Profit manager for Bybit V5 API.

## 🚀 Key Features
- **Real-Time Technical Scanner:** Multi-timeframe trend, momentum, RSI, Bollinger Bands, and Dip-buying setups.
- **Autonomous Take-Profit & Stop-Loss Engine:** Splitted exits (50% TP1 / 50% TP2) with 24/7 background Stop-Loss guard.
- **Unified Trading Account (UTA) Ready:** Direct Bybit Spot V5 integration with HMAC-SHA256 authenticated requests.
- **Interactive Lightweight Charts:** Visual entry lines, take-profit targets, and stop-loss levels.
- **One-Click Cloud Deployment:** Ready for Koyeb, Render, Railway, or any Docker container platform.

## ☁️ Deployment Instructions

### Koyeb (Recommended - 24/7 Always Free)
1. Fork or import this repository on [GitHub](https://github.com).
2. Connect your GitHub account to [Koyeb](https://www.koyeb.com).
3. Select this repository (`pepo-screener`).
4. Koyeb will automatically detect the `Dockerfile`.
5. Set Environment Variables:
   - `PORT`: `5600`
   - `BYBIT_API_KEY`: `your_bybit_api_key`
   - `BYBIT_API_SECRET`: `your_bybit_api_secret`
6. Click **Deploy**!

### Render
1. Create a new **Web Service** on [Render](https://render.com).
2. Connect this repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python server.py`
5. Set Environment Variables:
   - `PORT`: `10000`
   - `BYBIT_API_KEY`: `your_bybit_api_key`
   - `BYBIT_API_SECRET`: `your_bybit_api_secret`
6. Click **Create Web Service**!
