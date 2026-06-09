#!/usr/bin/env bash
# Install k3s control plane on vlinux1 (192.168.96.200)
set -euo pipefail

MASTER_IP="192.168.96.200"

echo "==> Installing k3s server on ${MASTER_IP}"

curl -sfL https://get.k3s.io | sh -s - server \
  --node-ip="${MASTER_IP}" \
  --advertise-address="${MASTER_IP}" \
  --tls-san="${MASTER_IP}" \
  --write-kubeconfig-mode=644

echo "==> Waiting for k3s to become ready..."
until sudo k3s kubectl get nodes &>/dev/null; do
  sleep 3
done

echo "==> Waiting for node to reach Ready state..."
sudo k3s kubectl wait node --all --for=condition=Ready --timeout=120s

echo ""
echo "==> Cluster nodes:"
sudo k3s kubectl get nodes -o wide

echo ""
echo "==> Kubeconfig is at /etc/rancher/k3s/k3s.yaml (world-readable)"
echo "    To use kubectl from this node:"
echo "      export KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
echo "    To copy to your laptop (varunlaptop):"
echo "      scp vlinux1:/etc/rancher/k3s/k3s.yaml ~/.kube/athena.yaml"
echo "      # Then replace 127.0.0.1 with 192.168.96.200 in the copied file"

echo ""
echo "==> Node token for worker join (pass to install-k3s-worker.sh):"
sudo cat /var/lib/rancher/k3s/server/node-token

echo ""
echo "==> Next steps:"
echo "    1. Run install-k3s-worker.sh on vlinux2 with label=inference"
echo "    2. Run install-k3s-worker.sh on xdev-sr  with label=ai"
echo "    3. Run label-nodes.sh from this node to apply workload labels"
