FROM wordpress:php8.3-apache

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip \
    && curl -fsSL \
       https://downloads.wordpress.org/plugin/woocommerce.latest-stable.zip \
       -o /tmp/woocommerce.zip \
    && unzip /tmp/woocommerce.zip \
       -d /usr/src/wordpress/wp-content/plugins/ \
    && rm /tmp/woocommerce.zip \
    && apt-get purge -y --auto-remove curl unzip \
    && rm -rf /var/lib/apt/lists/*