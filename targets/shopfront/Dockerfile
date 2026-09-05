# Storefront image.
#
# The build context is the repository root because the service depends on the internal
# telemetry package, which lives beside it; the layout under /srv mirrors the checkout so
# that the `file:` dependency resolves the same way it does in development.
#
# Version pins are exact. A floating tag changes the runtime under a service whose
# behaviour is measured in milliseconds by the carrier integration tests, and "it works
# on the tag we happened to pull" is not a deployment story anybody wants to defend.

FROM node:22.20.0-alpine3.21 AS deps
WORKDIR /srv/targets/shopfront
COPY sdk/node/package.json /srv/sdk/node/package.json
COPY sdk/node/dist /srv/sdk/node/dist
COPY targets/shopfront/package.json targets/shopfront/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:22.20.0-alpine3.21 AS web
WORKDIR /srv/targets/shopfront
COPY sdk/node/package.json /srv/sdk/node/package.json
COPY sdk/node/dist /srv/sdk/node/dist
COPY targets/shopfront/package.json targets/shopfront/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY targets/shopfront/web ./web
RUN npm run build:web

FROM node:22.20.0-alpine3.21 AS runtime
ENV NODE_ENV=production
WORKDIR /srv/targets/shopfront

RUN addgroup -g 10001 -S storefront \
 && adduser -u 10001 -S -G storefront -h /srv storefront \
 && mkdir -p /var/lib/storefront/media /var/lib/storefront/keys \
 && chown -R storefront:storefront /var/lib/storefront

COPY --from=deps /srv/sdk/node /srv/sdk/node
COPY --from=deps /srv/targets/shopfront/node_modules ./node_modules
COPY targets/shopfront/package.json ./package.json
COPY targets/shopfront/server ./server
COPY targets/shopfront/bin ./bin
COPY --from=web /srv/targets/shopfront/web/dist ./web/dist

# The reset command is an operator tool, not a route: it is run with `exec` against a
# running container when an estate has to be put back to its published state. The script
# itself stays beside the code it imports; what goes on PATH is a two-line launcher, so
# that its relative imports resolve whichever way Node is feeling about symbolic links.
RUN printf '#!/bin/sh\nexec node /srv/targets/shopfront/bin/state-reset "$@"\n' \
      > /usr/local/bin/state-reset \
 && chmod 0755 /usr/local/bin/state-reset /srv/targets/shopfront/bin/state-reset

USER storefront
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD wget -qO- http://127.0.0.1:3000/api/status >/dev/null || exit 1
CMD ["node", "server/index.js"]
