#!/bin/bash
# =============================================================================
# FraudShield — EC2 Bootstrap Script
# Tested on: Ubuntu 22.04 LTS (t3.large)
#
# Run as root or with sudo:
#   chmod +x bootstrap.sh && sudo ./bootstrap.sh
#
# What this does:
#   1. Installs Docker, kubectl, K3s
#   2. Builds service images
#   3. Imports images into K3s
#   4. Deploys the full stack to Kubernetes
#   5. Prints the public URL when done
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')] ✓ $1${NC}"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠ $1${NC}"; }
info() { echo -e "${BLUE}[$(date '+%H:%M:%S')] → $1${NC}"; }
err()  { echo -e "${RED}[$(date '+%H:%M:%S')] ✗ $1${NC}"; exit 1; }

# ── 1. System update ──────────────────────────────────────────────────────────
info "Updating system packages..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq curl git unzip jq net-tools
log "System packages updated."

# ── 2. Docker ─────────────────────────────────────────────────────────────────
info "Installing Docker..."
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker ubuntu
  systemctl enable docker && systemctl start docker
  log "Docker installed."
else
  log "Docker already installed — skipping."
fi

# ── 3. K3s ────────────────────────────────────────────────────────────────────
info "Installing K3s (lightweight Kubernetes)..."
if ! command -v k3s &>/dev/null; then
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" sh -
  # Give K3s a moment to fully start
  sleep 15
  log "K3s installed."
else
  log "K3s already installed — skipping."
fi

# Set up kubectl config for ubuntu user
mkdir -p /home/ubuntu/.kube
cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
chown ubuntu:ubuntu /home/ubuntu/.kube/config
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Verify K3s is healthy
kubectl get nodes || err "K3s failed to start. Check: journalctl -u k3s"
log "K3s is healthy."

# ── 4. Clone repository ───────────────────────────────────────────────────────
info "Cloning FraudShield repository..."
cd /home/ubuntu

# Replace with your actual GitHub repo URL before running
REPO_URL="${GITHUB_REPO:-https://github.com/YOUR_USERNAME/fraudshield.git}"

if [ -d "fraudshield" ]; then
  warn "fraudshield directory exists — pulling latest."
  cd fraudshield && git pull && cd ..
else
  git clone "$REPO_URL" fraudshield
fi

cd fraudshield
log "Repository ready."

# ── 5. Build Docker images ────────────────────────────────────────────────────
info "Building Transaction Service image..."
docker build -t fraudshield-transaction-service:1.0.0 ./transaction-service/
log "Transaction Service image built."

info "Building Fraud Detection Service image..."
docker build -t fraudshield-fraud-detection-service:1.0.0 ./fraud-detection-service/
log "Fraud Detection Service image built."

# ── 6. Import images into K3s ─────────────────────────────────────────────────
# K3s uses containerd, not Docker — images must be explicitly imported
info "Importing images into K3s containerd runtime..."
docker save fraudshield-transaction-service:1.0.0 | k3s ctr images import -
docker save fraudshield-fraud-detection-service:1.0.0 | k3s ctr images import -
log "Images imported into K3s."

# ── 7. Deploy to Kubernetes ───────────────────────────────────────────────────
info "Applying Kubernetes manifests..."

kubectl apply -f k8s/00-namespace-configmap.yaml
log "Namespace and ConfigMap applied."

kubectl apply -f k8s/01-secrets.yaml
log "Secrets applied."

kubectl apply -f k8s/02-kafka.yaml
info "Waiting for Kafka to be ready (this takes ~30s)..."
kubectl wait --for=condition=available --timeout=120s \
  deployment/zookeeper -n fraudshield
kubectl wait --for=condition=available --timeout=120s \
  deployment/kafka -n fraudshield
log "Kafka ready."

kubectl apply -f k8s/03-databases.yaml
info "Waiting for databases to be ready..."
kubectl wait --for=condition=available --timeout=120s \
  deployment/postgres-transactions -n fraudshield
kubectl wait --for=condition=available --timeout=120s \
  deployment/postgres-fraud -n fraudshield
kubectl wait --for=condition=available --timeout=120s \
  deployment/redis -n fraudshield
log "Databases ready."

kubectl apply -f k8s/04-services.yaml
info "Waiting for application services to be ready..."
kubectl wait --for=condition=available --timeout=180s \
  deployment/transaction-service -n fraudshield
kubectl wait --for=condition=available --timeout=180s \
  deployment/fraud-detection-service -n fraudshield
log "Application services ready."

# ── 8. Print summary ──────────────────────────────────────────────────────────
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  FraudShield deployed successfully on AWS EC2 + K3s!   ${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Transaction Service:    ${BLUE}http://${PUBLIC_IP}:30003${NC}"
echo -e "  Fraud Detection:        ${BLUE}http://${PUBLIC_IP}:30004${NC}"
echo -e "  Transaction API Docs:   ${BLUE}http://${PUBLIC_IP}:30003/docs${NC}"
echo -e "  Fraud Detection Docs:   ${BLUE}http://${PUBLIC_IP}:30004/docs${NC}"
echo ""
echo -e "  All pods:"
kubectl get pods -n fraudshield
echo ""
echo -e "${YELLOW}  Remember to open ports 30003 and 30004 in your EC2${NC}"
echo -e "${YELLOW}  Security Group if Postman requests time out.${NC}"
echo ""
echo -e "${YELLOW}  To tear down:  kubectl delete namespace fraudshield${NC}"
echo -e "${YELLOW}  To terminate:  AWS Console → EC2 → Terminate instance${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
