#!/bin/bash

set -euo pipefail

NS="fraudshield"
KAFKA_NS="kafka"
TXN_PORT="18003"
TXN_SERVICE_PORT="8003"
MERCHANT="blocked-merchant-demo"
USER_ID="fraud-smoke-test-user"
PORT_FORWARD_LOG="/tmp/fraudshield-transaction-port-forward.log"

PF_PID=""

cleanup()
{
    if [ -n "${PF_PID}" ] && kill -0 "${PF_PID}" 2>/dev/null; then
        kill "${PF_PID}" 2>/dev/null || true
    fi
}

trap cleanup EXIT

echo "Checking Kubernetes pods..."
kubectl get pods -n "${NS}"
kubectl get pods -n "${KAFKA_NS}"

echo "Checking Kafka topics..."
TOPICS=$(kubectl exec -n "${KAFKA_NS}" fraudshield-kafka-dual-role-0 -- \
    /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server localhost:9092 \
    --list)

echo "${TOPICS}" | grep -q "transactions.initiated"
echo "${TOPICS}" | grep -q "fraud.verdict"

echo "Seeding Redis blacklist..."
kubectl exec -n "${NS}" deployment/redis -- \
    redis-cli SADD merchant:blacklist "${MERCHANT}" >/dev/null

BLACKLISTED=$(kubectl exec -n "${NS}" deployment/redis -- \
    redis-cli SISMEMBER merchant:blacklist "${MERCHANT}" | tr -d '\r')

if [ "${BLACKLISTED}" != "1" ]; then
    echo "FAIL: Merchant was not found in Redis blacklist"
    exit 1
fi

echo "Checking local port ${TXN_PORT} is free..."
if ss -ltn | awk '{print $4}' | grep -q ":${TXN_PORT}$"; then
    echo "FAIL: localhost:${TXN_PORT} is already in use."
    echo "Stop the existing port-forward or choose another local port."
    exit 1
fi

echo "Starting Kubernetes port-forward on localhost:${TXN_PORT}..."
kubectl port-forward service/transaction-service "${TXN_PORT}:${TXN_SERVICE_PORT}" -n "${NS}" \
    > "${PORT_FORWARD_LOG}" 2>&1 &

PF_PID=$!

sleep 3

if ! kill -0 "${PF_PID}" 2>/dev/null; then
    echo "FAIL: port-forward did not start."
    cat "${PORT_FORWARD_LOG}" || true
    exit 1
fi

echo "Checking Transaction Service health through Kubernetes port-forward..."
curl -fsS "http://localhost:${TXN_PORT}/health" >/dev/null

echo "Submitting blacklisted transaction..."
RESPONSE=$(curl -fsS -X POST "http://localhost:${TXN_PORT}/transactions/" \
    -H "Content-Type: application/json" \
    -d "{
        \"user_id\": \"${USER_ID}\",
        \"merchant_id\": \"${MERCHANT}\",
        \"amount\": 500,
        \"currency\": \"INR\"
    }")

TXN_ID=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1])["id"])' "${RESPONSE}")

echo "Transaction ID: ${TXN_ID}"
echo "Waiting for asynchronous fraud verdict..."

RESULT=""
STATUS=""
VERDICT=""
SCORE=""

for attempt in {1..30}; do
    RESULT=$(curl -fsS "http://localhost:${TXN_PORT}/transactions/${TXN_ID}")

    STATUS=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1]).get("status"))' "${RESULT}")
    VERDICT=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1]).get("fraud_verdict"))' "${RESULT}")
    SCORE=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1]).get("fraud_score"))' "${RESULT}")

    if [ "${STATUS}" = "BLOCKED" ] && [ "${VERDICT}" = "BLOCK" ] && [ "${SCORE}" = "100.0" ]; then
        break
    fi

    echo "Attempt ${attempt}/30: status=${STATUS}, verdict=${VERDICT}, score=${SCORE}; waiting..."
    sleep 1
done

echo "Checking transaction result..."

python3 -c '
import json
import sys

data = json.loads(sys.argv[1])

print("Transaction result:")
print(json.dumps(data, indent=2))

status = data.get("status")
score = data.get("fraud_score")
verdict = data.get("fraud_verdict")

if status != "BLOCKED":
    print(f"FAIL: expected status BLOCKED, got {status}")
    sys.exit(1)

if verdict != "BLOCK":
    print(f"FAIL: expected fraud_verdict BLOCK, got {verdict}")
    sys.exit(1)

if float(score) != 100.0:
    print(f"FAIL: expected fraud_score 100.0, got {score}")
    sys.exit(1)

print("PASS: Transaction was blocked correctly.")
' "${RESULT}"

echo "FraudShield fraud-block smoke test PASSED."
