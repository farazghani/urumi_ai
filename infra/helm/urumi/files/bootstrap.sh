#!/usr/bin/env bash
set -euo pipefail
cd /var/www/html
for attempt in $(seq 1 120); do
    if [ -f wp-config.php ] && wp db check --quiet --ssl=0 >/dev/null 2>&1; then break; fi
    sleep 2
done
wp db check --quiet --ssl=0
if ! wp core is-installed; then
    # Password travels via stdin, not process arguments or logs.
    printf '%s\n' "$WORDPRESS_ADMIN_PASSWORD" | wp core install \
      --url="$WORDPRESS_PUBLIC_URL" --title="$WORDPRESS_TITLE" \
      --admin_user="$WORDPRESS_ADMIN_USER" --admin_email="$WORDPRESS_ADMIN_EMAIL" \
      --prompt=admin_password --skip-email
fi
wp plugin activate woocommerce
wp eval-file /bootstrap/bootstrap.php
echo 'WordPress, WooCommerce and the infrastructure API key are configured.'
