#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
tag=${IMAGE_TAG:-local}
registry=${IMAGE_REGISTRY:-}
prefix=${registry:+${registry%/}/}

build() {
    local image=$1 context=$2
    if [ -n "${PLATFORMS:-}" ]; then
        docker buildx build --platform "$PLATFORMS" --push -t "${prefix}${image}:${tag}" "$repo/$context"
    else
        docker build -t "${prefix}${image}:${tag}" "$repo/$context"
    fi
}
build urumi-wordpress woocommerce_app/wordpress
build urumi-woo-mcp woocommerce_app/mcp
build urumi-dashboard-backend Dashboard/backend
build urumi-dashboard-frontend Dashboard/frontend
