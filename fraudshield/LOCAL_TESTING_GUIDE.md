# FraudShield — Local Testing Guide

## Prerequisites

Make sure these are installed on your Ubuntu machine:

```bash
# Docker
docker --version        # should be 24+
docker compose version  # should be 2.x

# Python (for running tests)
python3 --version       # should be 3.12+
```

---

## Step 1 — Start the full stack

```bash
# From the fraudshield/ root directory
docker compose up --build
```

First run takes ~3–4 minutes (downloads images, builds services).
Subsequent runs take ~30 seconds.

**You should see logs like:**
```
fraudshield-kafka-init      | Created topic transactions.initiated
fraudshield-kafka-init      | Created topic fraud.verdict
fraudshield-transaction-service | INFO | Starting Transaction Service...
fraudshield-fraud-detection-service | INFO | Fraud detection consumer subscribed
```

---

## Step 2 — Verify all containers are healthy

Open a second terminal:

```bash
docker compose ps
```

All services should show `healthy`. If Kafka shows `starting`, wait 30 more seconds.

---

## Step 3 — Check API docs

Open your browser:

| Service | URL |
|---|---|
| Transaction Service | http://localhost:8003/docs |
| Fraud Detection Service | http://localhost:8004/docs |

You should see the FastAPI Swagger UI for both.

---

## Step 4 — Import Postman collection

1. Open Postman
2. Click **Import** → select `FraudShield.postman_collection.json`
3. The collection variables are pre-set to `localhost`

---

## Step 5 — Run the demo test sequence

### Test A: Normal transaction → PASS

Run request **"1. Initiate — Normal Transaction"**

Response (202):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "amount": 1500.0,
  ...
}
```

Wait 2–3 seconds, then run **"2. Get Transaction Status"**

Response:
```json
{
  "status": "COMPLETED",
  "fraud_verdict": "PASS",
  "fraud_score": 15.0,
  ...
}
```

---

### Test B: High amount + new merchant → REVIEW/BLOCK

Run request **"3. Initiate — High Amount Transaction"**

Wait 2–3 seconds, then check status. Expected:
```json
{
  "status": "FLAGGED",
  "fraud_verdict": "REVIEW",
  "fraud_score": 50.0
}
```

---

### Test C: Blacklisted merchant → BLOCK

Run request **"4. Initiate — Blacklisted Merchant"**

> Note: First seed the blacklist by running this in your terminal:
> ```bash
> docker exec fraudshield-redis redis-cli SADD merchant:blacklist fraud_merchant_blacklisted
> ```

Expected:
```json
{
  "status": "BLOCKED",
  "fraud_verdict": "BLOCK",
  "fraud_score": 100.0
}
```

---

### Test D: Velocity rule → BLOCK

Use Postman Runner to send request **"5. Velocity Test"** 6+ times.

After the 6th transaction, the verdict should escalate to REVIEW or BLOCK.

---

## Step 6 — Run unit tests

```bash
# Transaction Service tests
cd transaction-service
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v

# Fraud Detection Service tests
cd ../fraud-detection-service
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

Expected output:
```
tests/test_scoring.py::TestAmountRule::test_triggers_when_amount_exceeds_3x_average PASSED
tests/test_scoring.py::TestVelocityRule::test_triggers_above_limit PASSED
...
18 passed in 0.42s
```

---

## Step 7 — Watch the Kafka events flow (optional but impressive)

```bash
# Watch transactions.initiated topic
docker exec fraudshield-kafka \
  kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic transactions.initiated \
  --from-beginning

# Watch fraud.verdict topic (open another terminal)
docker exec fraudshield-kafka \
  kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic fraud.verdict \
  --from-beginning
```

You'll see the raw JSON events flowing in real time. Great for the demo video.

---

## Tear down

```bash
docker compose down          # stop containers, keep volumes
docker compose down -v       # stop containers + delete all data
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Kafka not ready | Wait 30–60s, it's slow to start |
| Service exits immediately | Run `docker compose logs transaction-service` |
| Port already in use | Run `sudo lsof -i :8003` and kill the process |
| Transaction stuck on PENDING | Fraud Detection may have crashed — check `docker compose logs fraud-detection-service` |
