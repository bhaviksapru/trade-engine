FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Required at runtime (set via ECS task definition environment or SSM):
#   ORCHESTRATOR_API_KEY  - key NinjaTrader sends in X-API-Key header
#   SIGNAL_LAMBDA_URL     - API Gateway invoke URL, e.g. https://xxxx.execute-api.us-east-2.amazonaws.com/prod/signal
#   SIGNAL_API_KEY        - matches the secret in trade-engine/api-key-trade-engine (Secrets Manager)
#   MAX_POSITION_SIZE     - soft cap before Lambda hard-limit
#   MAX_DAILY_LOSS        - soft cap before Lambda hard-limit
#   ORDER_COOLDOWN_SECONDS

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
