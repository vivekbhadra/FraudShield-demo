#!/bin/bash
# =============================================================================
# FraudShield — Minikube Deployment Script
#
# Builds both service images inside the Minikube Docker daemon, applies all
# Kubernetes manifests in the correct order, and waits for every deployment
# to become fully healthy before exiting.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh                  # deploy from current directory
#   ./deploy.sh /path/to/fraudshield   # deploy from explicit path
#
# Prerequisites:
#   - minikube (running)
#   - kubectl (configured for minikube)
#   - docker
# =============================================================================

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}══ $* ══${RESET}"; }

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Script lives inside the fraudshield/ directory, so FRAUDSHIELD_DIR = SCRIPT_DIR
# Optionally override by passing an explicit path: ./deploy.sh /path/to/fraudshield
FRAUDSHIELD_DIR="${1:-${SCRIPT_DIR}}"

NS="fraudshield"

TXN_IMAGE="fraudshield-transaction:1.0.0"
FRAUD_IMAGE="fraudshield-fraud-detection:1.0.2"

TXN_BUILD_CTX="${FRAUDSHIELD_DIR}/transaction-service"
FRAUD_BUILD_CTX="${FRAUDSHIELD_DIR}/fraud-detection-service"

MANIFESTS_DIR="${FRAUDSHIELD_DIR}/k8s"
TXN_K8S_DIR="${FRAUDSHIELD_DIR}/transaction-service/k8s"
FRAUD_K8S_DIR="${FRAUDSHIELD_DIR}/fraud-detection-service/k8s"

# Timeouts
ROLLOUT_TIMEOUT="300s"      # for Kafka / Zookeeper / app services
DB_WAIT_TIMEOUT=240         # seconds — Postgres needs longer on a cold PVC

# ── wait_for_deployment <name> <namespace> <timeout_seconds> ─────────────────
# Polls until all pods in the deployment are Ready, printing pod status each
# cycle. Falls back gracefully if the deployment doesn't exist yet.
wait_for_deployment() {
    local name="$1" ns="$2" timeout="$3"
    local elapsed=0 interval=5

    info "Waiting for ${name} (up to ${timeout}s)..."
    while true; do
        local ready total
        ready=$(kubectl get deployment "${name}" -n "${ns}" \
                  -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
        total=$(kubectl get deployment "${name}" -n "${ns}" \
                  -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "?")
        ready="${ready:-0}"

        if [[ "${ready}" == "${total}" && "${total}" != "0" && "${total}" != "?" ]]; then
            success "${name} ready (${ready}/${total} pods)."
            return 0
        fi

        # Show pod-level status so the user isn't staring at a blank screen
        local pod_status
        pod_status=$(kubectl get pods -n "${ns}" -l "app=${name}" \
                       --no-headers 2>/dev/null \
                     | awk '{print $1, $3, $4}' | head -3 || true)
        warn "  ${name}: ${ready:-0}/${total} ready — ${pod_status:-no pods yet} (${elapsed}s elapsed)"

        if (( elapsed >= timeout )); then
            error "${name} did not become ready within ${timeout}s."
            error "Pod details:"
            kubectl describe pod -n "${ns}" -l "app=${name}" | tail -30 || true
            error "Pod logs:"
            kubectl logs -n "${ns}" -l "app=${name}" --tail=20 2>/dev/null || true
            exit 1
        fi

        sleep "${interval}"
        elapsed=$(( elapsed + interval ))
    done
}

# ── Preflight checks ──────────────────────────────────────────────────────────
header "Preflight Checks"

for cmd in minikube kubectl docker; do
    if ! command -v "${cmd}" &>/dev/null; then
        error "'${cmd}' is not installed or not on PATH."
        exit 1
    fi
    success "${cmd} found: $(${cmd} version --short 2>/dev/null || ${cmd} --version 2>/dev/null | head -1)"
done

if ! minikube status --format='{{.Host}}' 2>/dev/null | grep -q "Running"; then
    warn "Minikube is not running. Starting it now..."
    minikube start
    success "Minikube started."
else
    success "Minikube is running."
fi

if [[ ! -d "${FRAUDSHIELD_DIR}" ]]; then
    error "Could not find fraudshield directory at: ${FRAUDSHIELD_DIR}"
    error "Run this script from inside the fraudshield/ directory, or pass it as an argument:"
    error "  ./deploy.sh /path/to/FraudShield-demo/fraudshield"
    exit 1
fi

# ── Build images inside Minikube's Docker daemon ──────────────────────────────
header "Building Docker Images (inside Minikube)"

info "Switching Docker context to Minikube's daemon..."
eval "$(minikube docker-env)"
success "Docker now points to Minikube's internal registry."

info "Building transaction-service image: ${TXN_IMAGE}"
docker build -t "${TXN_IMAGE}" "${TXN_BUILD_CTX}"
success "Built ${TXN_IMAGE}"

info "Building fraud-detection-service image: ${FRAUD_IMAGE}"
docker build -t "${FRAUD_IMAGE}" "${FRAUD_BUILD_CTX}"
success "Built ${FRAUD_IMAGE}"

# Confirm images are visible to Minikube
info "Images available in Minikube:"
docker images | grep "fraudshield" || true

# ── Apply Kubernetes manifests ────────────────────────────────────────────────
header "Applying Kubernetes Manifests"

# 1. Namespace + base ConfigMap
info "Applying namespace and ConfigMap..."
kubectl apply -f "${MANIFESTS_DIR}/00-namespace-configmap.yaml"
success "Namespace '${NS}' ready."

# 2. Secrets
info "Applying secrets..."
kubectl apply -f "${MANIFESTS_DIR}/01-secrets.yaml"
success "Secrets applied."

# 3. Infrastructure: Kafka + Zookeeper
info "Deploying Kafka and Zookeeper..."
kubectl apply -f "${MANIFESTS_DIR}/02-kafka.yaml"

# 4. Infrastructure: Postgres (x2) + Redis
# Re-apply secrets first — a previous failed run may have left them clobbered
info "Re-applying secrets (ensuring postgres-password key is present)..."
kubectl apply -f "${MANIFESTS_DIR}/01-secrets.yaml"
info "Deploying PostgreSQL and Redis..."
kubectl apply -f "${MANIFESTS_DIR}/03-databases.yaml"

# NOTE: 05-runtime-config.yaml is intentionally NOT applied here.
# It was written for a Strimzi Kafka operator setup and overwrites
# fraudshield-secrets with wrong hostnames/passwords, breaking Postgres.

# ── Wait for infrastructure to be ready ──────────────────────────────────────
header "Waiting for Infrastructure"

info "Waiting for Zookeeper..."
kubectl rollout status deployment/zookeeper -n "${NS}" --timeout="${ROLLOUT_TIMEOUT}"
success "Zookeeper ready."

info "Waiting for Kafka..."
kubectl rollout status deployment/kafka -n "${NS}" --timeout="${ROLLOUT_TIMEOUT}"
success "Kafka ready."

info "Waiting for postgres-transactions..."
wait_for_deployment postgres-transactions "${NS}" "${DB_WAIT_TIMEOUT}"

info "Waiting for postgres-fraud..."
wait_for_deployment postgres-fraud "${NS}" "${DB_WAIT_TIMEOUT}"

info "Waiting for Redis..."
wait_for_deployment redis "${NS}" "${DB_WAIT_TIMEOUT}"

# ── Deploy application services ───────────────────────────────────────────────
header "Deploying Application Services"

info "Deploying transaction-service..."
kubectl apply -f "${TXN_K8S_DIR}/deployment.yaml"
kubectl apply -f "${TXN_K8S_DIR}/service.yaml"
success "transaction-service manifests applied."

info "Deploying fraud-detection-service..."
kubectl apply -f "${FRAUD_K8S_DIR}/fraud-detection.yaml"
success "fraud-detection-service manifests applied."

# NOTE: 04-services.yaml is intentionally NOT applied here.
# It references wrong image names and was written for a cloud environment.
# The per-service k8s/ manifests are the correct source of truth for Minikube.

# Scale app services to 1 replica — Minikube doesn't need 2, and it avoids
# resource pressure on a single-node cluster
info "Scaling deployments to 1 replica for Minikube..."
kubectl scale deployment/transaction-service   -n "${NS}" --replicas=1
kubectl scale deployment/fraud-detection-service -n "${NS}" --replicas=1
success "Replicas set to 1."

# ── Wait for application services ─────────────────────────────────────────────
header "Waiting for Application Services"

info "Waiting for transaction-service rollout..."
wait_for_deployment transaction-service "${NS}" 180
info "Waiting for fraud-detection-service rollout..."
wait_for_deployment fraud-detection-service "${NS}" 180

# ── Health checks ─────────────────────────────────────────────────────────────
header "Health Checks"

LOCAL_TXN_PORT=18003
REMOTE_TXN_PORT=8003
TXN_URL="http://localhost:${LOCAL_TXN_PORT}"

warn "Never test Kubernetes transaction-service through localhost:8003."
info "Using explicit Kubernetes port-forward only."
info "Forwarding service/transaction-service ${LOCAL_TXN_PORT}:${REMOTE_TXN_PORT} in namespace ${NS}..."

if command -v lsof &>/dev/null; then
    info "Checking whether localhost:8003 is occupied..."
    sudo lsof -i :8003 || true
fi

kubectl port-forward "service/transaction-service" "${LOCAL_TXN_PORT}:${REMOTE_TXN_PORT}" -n "${NS}" \
    >/tmp/fraudshield-transaction-port-forward.log 2>&1 &

PORT_FORWARD_PID=$!

cleanup_port_forward() {
    if kill -0 "${PORT_FORWARD_PID}" 2>/dev/null; then
        kill "${PORT_FORWARD_PID}" 2>/dev/null || true
    fi
}
trap cleanup_port_forward EXIT

sleep 5

info "Probing transaction-service health at ${TXN_URL}/health ..."

MAX_RETRIES=15
RETRY_INTERVAL=5

for i in $(seq 1 "${MAX_RETRIES}"); do
    HTTP_CODE=$(curl -o /dev/null -s -w "%{http_code}" --max-time 5 "${TXN_URL}/health" || echo "000")

    if [[ "${HTTP_CODE}" == "200" ]]; then
        success "transaction-service health check passed through Kubernetes port-forward."
        break
    fi

    if [[ "${i}" -eq "${MAX_RETRIES}" ]]; then
        error "transaction-service did not become healthy through Kubernetes port-forward."
        error "Last HTTP code: ${HTTP_CODE}"
        error "Port-forward log:"
        cat /tmp/fraudshield-transaction-port-forward.log || true
        error "Check logs with:"
        error "  kubectl logs -n ${NS} deployment/transaction-service"
        exit 1
    fi

    warn "Attempt ${i}/${MAX_RETRIES} — got HTTP ${HTTP_CODE}, retrying in ${RETRY_INTERVAL}s..."
    sleep "${RETRY_INTERVAL}"
done
header "Deployment Complete"

echo ""
echo -e "${BOLD}Cluster state:${RESET}"
kubectl get pods -n "${NS}"
echo ""
echo -e "${BOLD}Service endpoints:${RESET}"
echo -e "  Transaction Service   →  ${CYAN}${TXN_URL}${RESET}"
echo -e "  Fraud Detection       →  ${CYAN}internal Kubernetes service: fraud-detection-service:8004${RESET}"
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
echo -e "  minikube dashboard"
echo ""
echo -e "${GREEN}${BOLD}FraudShield is up and healthy.${RESET}"
