#!/bin/sh
# Bring the web container up.
set -eu

for dir in /var/tmp/telemetry /var/lib/php/sessions /var/spool/braithwaite \
           /var/www/html/uploads /var/www/literature /var/www/statements; do
    mkdir -p "$dir"
    chown www-data:www-data "$dir"
done

# The record drain does all of the network work for the observability agent, so that a
# served request never waits on anything remote. It is a separate process on purpose.
su -s /bin/sh -c '/var/www/vendor/bin/telemetry-drain --quiet' www-data &

# A container that has come up against an empty database has nothing to serve, so the
# trading data is built once. An existing database is left exactly as it is: restarting
# a container must not throw away what is in it.
if php /var/www/app/seed/needs-build.php; then
    echo "building the trading database" >&2
    /usr/local/bin/state-reset >/dev/null
fi

exec apache2-foreground
