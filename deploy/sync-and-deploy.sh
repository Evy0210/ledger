#!/usr/bin/env bash
# One-command deploy: rsync source to the K3s node, build both images there,
# import into containerd, apply manifests, restart.
#
#   cp deploy/local.env.example deploy/local.env   # once; fill in your values
#   bash deploy/sync-and-deploy.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 个人配置（服务器、域名、邮箱白名单）放在 deploy/local.env，不进 git。
if [[ -f "$REPO_ROOT/deploy/local.env" ]]; then
  set -a; source "$REPO_ROOT/deploy/local.env"; set +a
fi
: "${SSH_HOST:?set SSH_HOST (e.g. root@1.2.3.4) in deploy/local.env}"
: "${LEDGER_HOST:?set LEDGER_HOST (e.g. ledger.example.com) in deploy/local.env}"
export MAIL_ALLOWED_SENDERS="${MAIL_ALLOWED_SENDERS:-}" MAIL_INBOUND_ADDRESS="${MAIL_INBOUND_ADDRESS:-}" RECIPES_URL="${RECIPES_URL:-}"
REMOTE_DIR="${REMOTE_DIR:-/opt/ledger}"

echo "==> render deploy/k8s.yaml → deploy/k8s.rendered.yaml"
python3 - "$REPO_ROOT/deploy/k8s.yaml" "$REPO_ROOT/deploy/k8s.rendered.yaml" <<'PY'
import os, re, sys
src = open(sys.argv[1]).read()
out = re.sub(r"\$\{(\w+)\}", lambda m: os.environ[m.group(1)], src)
open(sys.argv[2], "w").write(out)
PY

echo "==> rsync source to $SSH_HOST:$REMOTE_DIR"
rsync -az --delete \
  --exclude '.git/' --exclude 'data/' --exclude 'node_modules/' \
  --exclude '.next/' --exclude '.venv/' --exclude '__pycache__/' \
  "$REPO_ROOT/" "$SSH_HOST:$REMOTE_DIR/"

echo "==> build + import images on the node"
ssh "$SSH_HOST" bash -s <<EOF
set -euo pipefail
cd "$REMOTE_DIR"
docker build -f deploy/backend.Dockerfile  -t ledger-backend:latest .
docker build -f deploy/frontend.Dockerfile -t ledger-frontend:latest .
docker save ledger-backend:latest  | k3s ctr images import -
docker save ledger-frontend:latest | k3s ctr images import -
kubectl apply -f deploy/k8s.rendered.yaml
# Secret is applied ONLY on first deploy (or SEED_SECRET=1): the live secret is
# patched in-place on the server (LLM keys) and must not be clobbered by the
# placeholder values in the local deploy/secret.yaml.
if ! kubectl -n ledger get secret ledger-secrets >/dev/null 2>&1 || [ "${SEED_SECRET:-0}" = "1" ]; then
  kubectl apply -f deploy/secret.yaml
fi
kubectl -n ledger rollout restart deploy/ledger-backend deploy/ledger-demo-backend deploy/ledger-frontend
kubectl -n ledger rollout status deploy/ledger-backend --timeout=180s
kubectl -n ledger rollout status deploy/ledger-demo-backend --timeout=180s
kubectl -n ledger rollout status deploy/ledger-frontend --timeout=180s
EOF

echo "==> done. https://$LEDGER_HOST"
