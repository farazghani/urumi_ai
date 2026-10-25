FROM wordpress:cli-php8.3@sha256:661db880e953da051abcac6c0a769767b995a82cd52101c003ca7438dd427d7d AS wpcli

FROM wordpress:php8.3-apache@sha256:d42f63cb89ad0eab272c4b393164e63830d0df270009907919f7dcce52b1a20b

ARG WOOCOMMERCE_VERSION=11.2.1
COPY --from=wpcli /usr/local/bin/wp /usr/local/bin/wp

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip default-mysql-client \
    && curl -fsSL \
       "https://downloads.wordpress.org/plugin/woocommerce.${WOOCOMMERCE_VERSION}.zip" \
       -o /tmp/woocommerce.zip \
    && unzip /tmp/woocommerce.zip \
       -d /usr/src/wordpress/wp-content/plugins/ \
    && rm /tmp/woocommerce.zip \
    && apt-get purge -y --auto-remove curl unzip \
    && rm -rf /var/lib/apt/lists/*
