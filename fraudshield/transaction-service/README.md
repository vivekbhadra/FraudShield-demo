# FraudShield — Transaction Service

Handles payment ingestion for the FraudShield platform. Accepts transaction requests via REST, persists them to PostgreSQL, and publishes `TransactionInitiated` events to Kafka. Listens for `FraudVerdict` events and updates transaction status accordingly.

## Tech Stack
- Python 3.12 + FastAPI
- PostgreSQL (via SQLAlchemy)
- Apache Kafka (via confluent-kafka)

## Running Locally

```bash
# 1. Create virtual environment
python -m venv venv && source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables (or create a .env file)
export DATABASE_URL=postgresql://fraudshield:fraudshield@localhost:5432/transactions_db
export KAFKA_BROKER=localhost:9092

# 4. Start the service
uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload
```

API docs available at: http://localhost:8003/docs

## Running Tests

```bash
pytest tests/ -v
```

## Docker

```bash
docker build -t fraudshield-transaction-service:1.0.0 .
docker run -p 8003:8003 \
  -e DATABASE_URL=... \
  -e KAFKA_BROKER=... \
  fraudshield-transaction-service:1.0.0
```

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /transactions/ | Initiate a new transaction |
| GET | /transactions/{id} | Get transaction by ID |
| GET | /transactions/user/{user_id} | List user transactions |
| GET | /health | Health check |
