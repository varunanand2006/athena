#!/usr/bin/env bash
# Apply workload labels to worker nodes after they have joined the cluster.
# Run this from vlinux1 (or anywhere kubectl is configured against the cluster).
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

echo "==> Current nodes:"
kubectl get nodes -o wide

echo ""
echo "==> Waiting for vlinux2 and xdev-sr to be Ready..."
kubectl wait node/vlinux2 --for=condition=Ready --timeout=180s
kubectl wait node/xdev-sr  --for=condition=Ready --timeout=180s

echo ""
echo "==> Applying workload labels..."
kubectl label node vlinux2 workload=inference --overwrite
kubectl label node xdev-sr  workload=ai       --overwrite

echo ""
echo "==> Final node labels:"
kubectl get nodes --show-labels
