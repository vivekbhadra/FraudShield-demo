#!/bin/bash
# =============================================================================
# FraudShield — Clean Minikube Deployment Script
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}══ $* ══${RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAUDSHIELD_DIR="${1:-${SCRIPT_DIR}}"

NS="fraudshield"

TXN_IMAGE="fraudshield-transaction:1.0.0"
FRAUD_IMAGE="fraudshield-fraud-detection:1.0.2"

TXN_BUILD_CTX="${FRAUDSHIELD_DIR}/transaction-service"
FRAUD_BUILD_CTX="${FRAUDSHIELD_DIR}/fraud-detection-service"

MANIFESTS_DIR="${FRAUDSHIELD_DIR}/k8s"
TXN_K8S_DIR="${FRAUDSHIELD_DIR}/transaction-service/k8s"
FRAUD_K8S_FILE="${FRAUDSHIELD_DIR}/fraud-detection-service/k8s/fraud-detection.yaml"

ROLLOUT_TIMEOUT="300s"
WAIT_TIMEOUT=240

LOCAL_TXN_PORT=18003
REMOTE_TXN_PORT=8003
TXN_URL="http://localhost:${LOCAL_TXN_PORT}"

PORT_FORWARD_PID=""

cleanup() {
    if [[ -n "${PORT_FORWARD_PID}" ]] && kill -0 "${PORT_FORWARD_PID}" 2>/dev/null; then
        kill "${PORT_FORWARD_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

require_path() {
    if [[ ! -e "$1" ]]; then
        error "Required path not found: $1"
        exit 1
    fi
}

print_pod_logs_and_exit() {
    local app="$1"
    local ns="$2"

    error "${app} failed to become ready."
    kubectl get pods -n "${ns}" -l "app=${app}" -o wide || true

    local pods
    pods=$(kubectl get pods -n "${ns}" -l "app=${app}" --no-headers 2>/dev/null | awk '{print $1}' || true)

    for pod in ${pods}; do
        error "Describe for pod: ${pod}"
        kubectl describe pod -n "${ns}" "${pod}" | tail -100 || true

        error "Previous logs for pod: ${pod}"
        kubectl logs -n "${ns}" "${pod}" --previous --tail=160 2>/dev/null || true

        error "Current logs for pod: ${pod}"
        kubectl logs -n "${ns}" "${pod}" --tail=160 2>/dev/null || true
    done

    exit 1
}

fail_fast_if_crashing() {
    local app="$1"
    local ns="$2"

    local newest_pod
    newest_pod=$(kubectl get pods -n "${ns}" -l "app=${app}" \
        --sort-by=.metadata.creationTimestamp \
        -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || true)

    if [[ -z "${newest_pod}" ]]; then
        return 0
    fi

    local waiting_reason
    waiting_reason=$(kubectl get pod "${newest_pod}" -n "${ns}" \
        -o jsonpath='{.status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)

    local terminated_reason
    terminated_reason=$(kubectl get pod "${newest_pod}" -n "${ns}" \
        -o jsonpath='{.status.containerStatuses[0].state.terminated.reason}' 2>/dev/null || true)

    case "${waiting_reason}" in
        CrashLoopBackOff|ImagePullBackOff|ErrImagePull)
            print_pod_logs_and_exit "${app}" "${ns}"
            ;;
    esac

    case "${terminated_reason}" in
        Error|OOMKilled)
            print_pod_logs_and_exit "${app}" "${ns}"
            ;;
    esac
}

wait_for_deployment() {
    local app="$1"
    local ns="$2"
    local timeout="$3"
    local elapsed=0
    local interval=5

    info "Waiting for ${app} in namespace ${ns} for up to ${timeout}s..."

    while true; do
        local ready total
        ready=$(kubectl get deployment "${app}" -n "${ns}" \
            -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
        total=$(kubectl get deployment "${app}" -n "${ns}" \
            -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "?")

        ready="${ready:-0}"

        if [[ "${ready}" == "${total}" && "${total}" != "0" && "${total}" != "?" ]]; then
            success "${app} ready (${ready}/${total} pods)."
            return 0
        fi

        warn "${app}: ${ready}/${total} ready."

        kubectl get pods -n "${ns}" -l "app=${app}" --no-headers 2>/dev/null \
            | awk '{print $1, $2, $3, $4}' || true

        fail_fast_if_crashing "${app}" "${ns}"

        if (( elapsed >= timeout )); then
            print_pod_logs_and_exit "${app}" "${ns}"
        fi

        sleep "${interval}"
        elapsed=$((elapsed + interval))
    done
}

wait_for_deployment_soft() {
    local app="$1"
    local ns="$2"
    local timeout="$3"
    local elapsed=0
    local interval=5

    info "Waiting for ${app} in namespace ${ns} for up to ${timeout}s..."

    while true; do
        local ready total
        ready=$(kubectl get deployment "${app}" -n "${ns}" \
            -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
        total=$(kubectl get deployment "${app}" -n "${ns}" \
            -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "?")

        ready="${ready:-0}"

        if [[ "${ready}" == "${total}" && "${total}" != "0" && "${total}" != "?" ]]; then
            success "${app} ready (${ready}/${total} pods)."
            return 0
        fi

        warn "${app}: ${ready}/${total} ready."

        kubectl get pods -n "${ns}" -l "app=${app}" --no-headers 2>/dev/null \
            | awk '{print $1, $2, $3, $4}' || true

        if (( elapsed >= timeout )); then
            return 1
        fi

        sleep "${interval}"
        elapsed=$((elapsed + interval))
    done
}

patch_postgres_recreate_strategy() {
    local deployment="$1"

    info "Ensuring ${deployment} uses Recreate deployment strategy..."

    kubectl -n "${NS}" patch deployment "${deployment}" \
        --type='json' \
        -p='[
            {"op":"replace","path":"/spec/strategy","value":{"type":"Recreate"}}
        ]' >/dev/null

    success "${deployment} uses Recreate deployment strategy."
}

postgres_logs_contain_corruption() {
    local deployment="$1"
    local pod
    local logs

    pod=$(kubectl get pods -n "${NS}" -l "app=${deployment}" \
        --sort-by=.metadata.creationTimestamp \
        -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || true)

    if [[ -z "${pod}" ]]; then
        return 1
    fi

    logs="$(
        kubectl logs -n "${NS}" "${pod}" --previous --tail=200 2>/dev/null || true
        kubectl logs -n "${NS}" "${pod}" --tail=200 2>/dev/null || true
    )"

    grep -Eqi \
        'checkpoint request failed|xlog flush request|request to flush past end of generated WAL|database system is shut down' \
        <<< "${logs}"
}

recover_local_postgres_pvc() {
    local deployment="$1"
    local pvc="$2"

    warn "PostgreSQL corruption detected for ${deployment}."
    warn "This is a local Minikube environment. Recreating PVC ${pvc}."

    info "Scaling ${deployment} to zero..."
    kubectl -n "${NS}" scale deployment "${deployment}" --replicas=0

    info "Scaling old ReplicaSets for ${deployment} to zero..."
    kubectl -n "${NS}" scale rs -l "app=${deployment}" --replicas=0 || true

    info "Deleting existing ${deployment} pods..."
    kubectl -n "${NS}" delete pod -l "app=${deployment}" --force --grace-period=0 2>/dev/null || true

    info "Deleting corrupted PVC ${pvc}..."
    kubectl -n "${NS}" delete pvc "${pvc}" --ignore-not-found=true

    info "Re-applying database manifests to recreate PVC..."
    kubectl apply -f "${MANIFESTS_DIR}/03-databases.yaml"

    patch_postgres_recreate_strategy "${deployment}"

    info "Starting ${deployment} with fresh local PVC..."
    kubectl -n "${NS}" scale deployment "${deployment}" --replicas=1

    wait_for_deployment "${deployment}" "${NS}" "${WAIT_TIMEOUT}"

    success "${deployment} recovered successfully."
}

wait_for_postgres_with_recovery() {
    local deployment="$1"
    local pvc="$2"

    if wait_for_deployment_soft "${deployment}" "${NS}" "${WAIT_TIMEOUT}"; then
        return 0
    fi

    if postgres_logs_contain_corruption "${deployment}"; then
        recover_local_postgres_pvc "${deployment}" "${pvc}"
        return 0
    fi

    print_pod_logs_and_exit "${deployment}" "${NS}"
}

ensure_postgres_database() {
    local deployment="$1"
    local service="$2"
    local database="$3"

    info "Ensuring database '${database}' exists through service '${service}'..."

    local exists
    exists="$(kubectl exec -n "${NS}" "deployment/${deployment}" -- \
        env PGPASSWORD=fraudshield \
        psql -h "${service}" -U fraudshield -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${database}';" \
        | tr -d '[:space:]')"

    if [[ "${exists}" == "1" ]]; then
        success "Database '${database}' already exists through service '${service}'."
    else
        kubectl exec -n "${NS}" "deployment/${deployment}" -- \
            env PGPASSWORD=fraudshield \
            psql -h "${service}" -U fraudshield -d postgres -c "CREATE DATABASE ${database};"
        success "Database '${database}' created through service '${service}'."
    fi
}

header "Preflight Checks"

for cmd in minikube kubectl docker curl base64; do
    if ! command -v "${cmd}" >/dev/null 2>&1; then
        error "${cmd} is not installed or not on PATH."
        exit 1
    fi
    success "${cmd} found."
done

if ! minikube status --format='{{.Host}}' 2>/dev/null | grep -q "Running"; then
    warn "Minikube is not running. Starting Minikube..."
    minikube start
    success "Minikube started."
else
    success "Minikube is running."
fi

eval "$(minikube docker-env)"
kubectl config use-context minikube >/dev/null
success "kubectl context set to minikube."

require_path "${FRAUDSHIELD_DIR}"
require_path "${TXN_BUILD_CTX}/Dockerfile"
require_path "${FRAUD_BUILD_CTX}/Dockerfile"
require_path "${MANIFESTS_DIR}/00-namespace-configmap.yaml"
require_path "${MANIFESTS_DIR}/01-secrets.yaml"
require_path "${MANIFESTS_DIR}/02-kafka.yaml"
require_path "${MANIFESTS_DIR}/03-databases.yaml"
require_path "${TXN_K8S_DIR}/deployment.yaml"
require_path "${TXN_K8S_DIR}/service.yaml"
require_path "${FRAUD_K8S_FILE}"

header "Build Images inside Minikube"

info "Building ${TXN_IMAGE}..."
docker build -t "${TXN_IMAGE}" "${TXN_BUILD_CTX}"
success "Built ${TXN_IMAGE}."

info "Building ${FRAUD_IMAGE}..."
docker build -t "${FRAUD_IMAGE}" "${FRAUD_BUILD_CTX}"
success "Built ${FRAUD_IMAGE}."

header "Apply Base Manifests"

kubectl apply -f "${MANIFESTS_DIR}/00-namespace-configmap.yaml"
kubectl apply -f "${MANIFESTS_DIR}/01-secrets.yaml"

success "Namespace, ConfigMap and Secrets applied."

header "Validate Live Secrets"

EXPECTED_TXN_DB_URL="postgresql://fraudshield:fraudshield@postgres-transactions:5432/transactions_db"
EXPECTED_FRAUD_DB_URL="postgresql://fraudshield:fraudshield@postgres-fraud:5432/fraud_db"
EXPECTED_REDIS_URL="redis://redis-service:6379/0"

LIVE_TXN_DB_URL="$(kubectl get secret fraudshield-secrets -n "${NS}" -o jsonpath='{.data.transaction-db-url}' | base64 -d)"
LIVE_FRAUD_DB_URL="$(kubectl get secret fraudshield-secrets -n "${NS}" -o jsonpath='{.data.fraud-db-url}' | base64 -d)"
LIVE_REDIS_URL="$(kubectl get secret fraudshield-secrets -n "${NS}" -o jsonpath='{.data.redis-url}' | base64 -d)"

if [[ "${LIVE_TXN_DB_URL}" != "${EXPECTED_TXN_DB_URL}" ]]; then
    error "Bad transaction-db-url: ${LIVE_TXN_DB_URL}"
    exit 1
fi

if [[ "${LIVE_FRAUD_DB_URL}" != "${EXPECTED_FRAUD_DB_URL}" ]]; then
    error "Bad fraud-db-url: ${LIVE_FRAUD_DB_URL}"
    exit 1
fi

if [[ "${LIVE_REDIS_URL}" != "${EXPECTED_REDIS_URL}" ]]; then
    error "Bad redis-url: ${LIVE_REDIS_URL}"
    exit 1
fi

success "Live Secrets are correct."

header "Deploy Infrastructure"

kubectl apply -f "${MANIFESTS_DIR}/02-kafka.yaml"
kubectl apply -f "${MANIFESTS_DIR}/03-databases.yaml"

patch_postgres_recreate_strategy "postgres-transactions"
patch_postgres_recreate_strategy "postgres-fraud"

kubectl rollout status deployment/zookeeper -n "${NS}" --timeout="${ROLLOUT_TIMEOUT}"
kubectl rollout status deployment/kafka -n "${NS}" --timeout="${ROLLOUT_TIMEOUT}"

wait_for_postgres_with_recovery "postgres-transactions" "postgres-transactions-pvc"
ensure_postgres_database "postgres-transactions" "postgres-transactions" "transactions_db"

wait_for_postgres_with_recovery "postgres-fraud" "postgres-fraud-pvc"
ensure_postgres_database "postgres-fraud" "postgres-fraud" "fraud_db"

wait_for_deployment redis "${NS}" "${WAIT_TIMEOUT}"

header "Deploy Application Services"

kubectl apply -f "${TXN_K8S_DIR}/deployment.yaml"
kubectl apply -f "${TXN_K8S_DIR}/service.yaml"

kubectl apply -f "${FRAUD_K8S_FILE}"

info "Removing fraud-detection HPA for stable local Minikube deployment..."
kubectl delete hpa fraud-detection-hpa -n "${NS}" --ignore-not-found=true

info "Scaling app deployments to 1 replica..."
kubectl scale deployment/transaction-service -n "${NS}" --replicas=1
kubectl scale deployment/fraud-detection-service -n "${NS}" --replicas=1

info "Restarting app deployments after infrastructure recovery..."
kubectl rollout restart deployment/transaction-service -n "${NS}"
kubectl rollout restart deployment/fraud-detection-service -n "${NS}"

header "Wait for Application Services"

wait_for_deployment transaction-service "${NS}" "${WAIT_TIMEOUT}"
wait_for_deployment fraud-detection-service "${NS}" "${WAIT_TIMEOUT}"

header "Health Check through Trusted Port-Forward"

warn "Do not test Kubernetes transaction-service through localhost:8003."
warn "localhost:8003 may belong to a stale Docker or kubectl port-forward."

if command -v lsof >/dev/null 2>&1; then
    info "Checking whether trusted local port ${LOCAL_TXN_PORT} is already in use..."
    if lsof -i :"${LOCAL_TXN_PORT}" >/dev/null 2>&1; then
        error "localhost:${LOCAL_TXN_PORT} is already in use."
        error "Stop the existing process or use another port."
        lsof -i :"${LOCAL_TXN_PORT}" || true
        exit 1
    fi
else
    if ss -ltn | awk '{print $4}' | grep -q ":${LOCAL_TXN_PORT}$"; then
        error "localhost:${LOCAL_TXN_PORT} is already in use."
        error "Stop the existing process or use another port."
        exit 1
    fi
fi

rm -f /tmp/fraudshield-transaction-port-forward.log

info "Starting port-forward: service/transaction-service ${LOCAL_TXN_PORT}:${REMOTE_TXN_PORT}"
kubectl port-forward "service/transaction-service" "${LOCAL_TXN_PORT}:${REMOTE_TXN_PORT}" -n "${NS}" \
    > /tmp/fraudshield-transaction-port-forward.log 2>&1 &

PORT_FORWARD_PID=$!

info "Waiting for port-forward to become ready..."

for attempt in $(seq 1 20); do
    if ! kill -0 "${PORT_FORWARD_PID}" 2>/dev/null; then
        error "Port-forward process exited."
        error "Port-forward log:"
        cat /tmp/fraudshield-transaction-port-forward.log || true
        exit 1
    fi

    if grep -q "Forwarding from" /tmp/fraudshield-transaction-port-forward.log; then
        success "Port-forward is ready."
        break
    fi

    if [[ "${attempt}" -eq 20 ]]; then
        error "Port-forward did not become ready."
        error "Port-forward log:"
        cat /tmp/fraudshield-transaction-port-forward.log || true
        exit 1
    fi

    sleep 1
done

info "Probing ${TXN_URL}/health..."

for attempt in $(seq 1 15); do
    HTTP_CODE="$(curl -o /dev/null -s -w "%{http_code}" --max-time 5 "${TXN_URL}/health" || echo "000")"

    if [[ "${HTTP_CODE}" == "200" ]]; then
        success "Transaction Service health check passed at ${TXN_URL}/health."
        break
    fi

    warn "Health attempt ${attempt}/15 returned HTTP ${HTTP_CODE}."

    if [[ "${attempt}" -eq 15 ]]; then
        error "Health check failed. Last HTTP code: ${HTTP_CODE}"
        error "Port-forward log:"
        cat /tmp/fraudshield-transaction-port-forward.log || true
        error "transaction-service logs:"
        kubectl logs -n "${NS}" deployment/transaction-service --tail=120 || true
        exit 1
    fi

    sleep 5
done

header "Deployment Complete"

echo ""
echo -e "${BOLD}Cluster state:${RESET}"
kubectl get pods -n "${NS}"

echo ""
echo -e "${BOLD}Trusted endpoint:${RESET}"
echo -e "  Transaction Service -> ${CYAN}${TXN_URL}${RESET}"

echo ""
echo -e "${BOLD}Swagger UI:${RESET}"
echo -e "  Transaction Service:"
echo -e "  ${TXN_URL}/docs"
echo ""
echo -e "  Fraud Detection Service:"
echo -e "  kubectl port-forward service/fraud-detection-service 18004:8004 -n ${NS}"
echo -e "  http://localhost:18004/docs"

echo ""
echo -e "${BOLD}Useful commands:${RESET}"
echo -e "  kubectl get all -n ${NS}"
echo -e "  kubectl logs -n ${NS} deployment/transaction-service -f"
echo -e "  kubectl logs -n ${NS} deployment/fraud-detection-service -f"
echo -e "  kubectl port-forward service/transaction-service 18003:8003 -n ${NS}"
echo -e "  ./scripts/test_fraud_block.sh"
echo -e "  minikube dashboard"

echo ""
echo -e "${GREEN}${BOLD}FraudShield is deployed and healthy.${RESET}"
