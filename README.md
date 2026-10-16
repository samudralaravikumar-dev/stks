# Intraday CALL / PUT Signal Engine

Streamlit portal for intraday options on NSE indices, F&O stocks and MCX: signals, CALL/PUT recommendations with
targets, a NIFTY 50 scanner, a recommendation track record, options analytics (GEX, straddle-implied move, IV smile),
market intel, paper trading with a risk guard, and Telegram alerts. Data from Upstox (recommended), Zerodha Kite, or
free Yahoo + NSE as a fallback.

> Educational tool only, not financial advice. The signal rules have no proven edge yet: paper-trade first.

## Run on your PC
Double-click `run.bat` and open http://localhost:8502. Background paper trading and alerts: `run_engine.bat`.

## Deploy on Streamlit Community Cloud
1. Push this repository to GitHub **without** `.streamlit/secrets.toml`, `users.db`, `trading.db` or any `*.zip`.
2. Go to https://share.streamlit.io and choose **Create app → Deploy a public app from GitHub**.
3. Repository: this repo · Branch: `main` · Main file path: `trading_signals_app.py`.
4. **Advanced settings:** Python version **3.12**, and paste your secrets (same format as
   `.streamlit/secrets.toml.example`), for example:
   ```toml
   [upstox]
   analytics_token = "your-upstox-analytics-token"
   ```
5. Deploy. On the first visit, create the admin account.

Streamlit Cloud limits to know:
- Its disk is temporary: user accounts, the paper journal and the track record reset whenever the app restarts or
  redeploys. For permanent storage use the Docker deployment below (or an external database).
- `runner.py` (background alerts with the browser closed) does not run there: keep the Dashboard on auto-refresh.
- It runs in the US: some NSE website endpoints (events calendar, sector indices) may be blocked. Upstox market data
  works from anywhere.

## Deploy on your own server (always on, permanent data)
```bash
unzip trad_deploy.zip -d trad && cd trad
sudo bash deploy/setup_server.sh            # or: sudo bash deploy/setup_server.sh your.domain.com
```
Runs the portal, the background engine and automatic HTTPS with Docker; data persists in `./data`.

## Secrets (`.streamlit/secrets.toml`, never commit it)
See `.streamlit/secrets.toml.example`: `[upstox]` analytics token, `[kite]`, `[telegram]` alerts, `[sms]` OTP login.
