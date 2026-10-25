#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
for tool in docker k3d kubectl helm python3; do
    command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
docker info >/dev/null
if ! k3d cluster list -o json | python3 -c 'import json,sys; sys.exit(not any(c["name"] == "urumi" for c in json.load(sys.stdin)))'; then
    k3d cluster create --config "$repo/infra/local/k3d-config.yaml"
else
    k3d cluster start urumi
fi
# Keep the user's default kubeconfig and current context unchanged.
export KUBECONFIG
KUBECONFIG=$(k3d kubeconfig write urumi)
IMAGE_TAG=local IMAGE_REGISTRY= PLATFORMS= "$repo/infra/scripts/build-images.sh"
k3d image import -c urumi urumi-wordpress:local urumi-woo-mcp:local \
    urumi-dashboard-backend:local urumi-dashboard-frontend:local
kubectl create namespace urumi --dry-run=client -o yaml | kubectl apply -f -
python3 "$repo/infra/scripts/create-secrets.py" --namespace urumi
helm upgrade --install urumi "$repo/infra/helm/urumi" -n urumi \
    -f "$repo/infra/helm/urumi/values-local.yaml" --wait --timeout 15m
echo 'Ready: http://store.urumi.test and http://dashboard.urumi.test'
echo 'Map both hostnames to 127.0.0.1 in /etc/hosts (see infra/README.md).'
