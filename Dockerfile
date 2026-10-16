FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata \
    TRAD_DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8502
HEALTHCHECK --interval=60s --timeout=5s CMD curl -fs http://localhost:8502/_stcore/health || exit 1
CMD ["python", "-m", "streamlit", "run", "trading_signals_app.py", "--server.address=0.0.0.0", "--server.port=8502", "--server.headless=true"]
