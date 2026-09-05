# The trade site.
#
# Build context is the repository root, because the internal observability package is
# installed from source alongside the application rather than from a package server.
#
# The tags are exact on purpose. The interpreter's own settings -- what a header
# function will accept, what a stream wrapper will open, how a session identifier is
# handled -- change between minor versions, and this deployment is written against the
# ones in 8.3. A floating tag does not keep a deployment fresh, it changes what it does
# without anything changing here.
#
# Digests are not written in because this file was authored offline. At first build,
# record `docker image inspect --format '{{index .RepoDigests 0}}'` for each of the
# three and replace the tags with `image@sha256:...`.

FROM composer:2.8.4 AS deps
WORKDIR /build
# Only what is needed to install: the package's own manifest, its source and its
# command. Nothing else from that tree reaches the image.
COPY sdk/php/composer.json /build/packages/telemetry/composer.json
COPY sdk/php/src           /build/packages/telemetry/src
COPY sdk/php/bin           /build/packages/telemetry/bin
COPY targets/legacy/composer.json /build/composer.json
RUN composer install --no-dev --no-interaction --no-progress --prefer-dist --optimize-autoloader


FROM php:8.3.14-apache

RUN set -eux; \
    docker-php-ext-install -j"$(nproc)" pdo_mysql opcache; \
    a2enmod headers; \
    a2dissite 000-default; \
    rm -f /var/www/html/index.html

COPY targets/legacy/docker/site.conf /etc/apache2/sites-available/braithwaite.conf
RUN a2ensite braithwaite

COPY targets/legacy/docker/php.ini /usr/local/etc/php/conf.d/zz-site.ini

COPY --from=deps /build/vendor /var/www/vendor
COPY targets/legacy/app    /var/www/app
COPY targets/legacy/public /var/www/html

COPY targets/legacy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY targets/legacy/state-reset          /usr/local/bin/state-reset

RUN set -eux; \
    mkdir -p /var/www/literature /var/www/statements /var/www/html/uploads \
             /var/spool/braithwaite /var/lib/php/sessions /var/tmp/telemetry; \
    chown -R www-data:www-data /var/www /var/spool/braithwaite /var/lib/php/sessions /var/tmp/telemetry; \
    chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/state-reset /var/www/vendor/bin/telemetry-drain

EXPOSE 80

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
