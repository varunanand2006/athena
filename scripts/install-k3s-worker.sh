#!/usr/bin/env bash
# Install k3s agent on a worker node and join the cluster.
#
# Usage:
#   ./install-k3s-worker.sh <master-ip> <node-token> <workload-label>
#
# Examples:
#   On vlinux2:  ./install-k3s-worker.sh 192.168.96.200 <token> inference
#   On xdev-sr:  ./install-k3s-worker.sh 192.168.96.200 <token> ai
set -euo pipefail

MASTER_IP="${1:?Usage: $0 <master-ip> <node-token> <workload-label>}"
NODE_TOKEN="${2:?Usage: $0 <master-ip> <node-token> <workload-label>}"
WORKLOAD_LABEL="${3:?Usage: $0 <master-ip> <node-token> <workload-label>}"

echo "==> Joining cluster at ${MASTER_IP} with workload=${WORKLOAD_LABEL}"

curl -sfL https://get.k3s.io | \
  K3S_URL="https://${MASTER_IP}:6443" \
  K3S_TOKEN="${NODE_TOKEN}" \
  sh -s - agent \
    --node-label="workload=${WORKLOAD_LABEL}"

echo ""
echo "==> Agent installed. This node will appear in 'kubectl get nodes' shortly."
echo "    From vlinux1, run: sudo k3s kubectl get nodes -o wide"
echo ""
echo "==> Then run label-nodes.sh from vlinux1 to finalize node labels."
