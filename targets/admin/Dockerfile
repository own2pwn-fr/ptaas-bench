# Meridian operations console.
#
# Build context is the repository root, because the service links against the platform
# team's telemetry library, which lives beside it rather than in the internal registry
# this image has no credentials for.
#
#   docker build -f targets/admin/Dockerfile -t meridian-console .
#
# Three stages: the front end, the service, and a runtime that carries neither
# toolchain. Every tag is exact — a floating one would change what this deployment does
# without changing anything in the repository, and the version of the runtime is part of
# what the service's behaviour depends on.

# --------------------------------------------------------------------- front end
FROM node:22.14.0-bookworm-slim AS web

WORKDIR /build
COPY targets/admin/web/package.json targets/admin/web/package-lock.json ./
# ci rather than install: the lock file is what pins the tree, and a build that quietly
# resolved a different one would change the console without changing this repository.
RUN npm ci --no-audit --no-fund

COPY targets/admin/web/angular.json targets/admin/web/tsconfig.json \
     targets/admin/web/tsconfig.app.json ./
COPY targets/admin/web/src ./src
COPY targets/admin/web/public ./public
RUN npx ng build --configuration production

# ----------------------------------------------------------------------- service
FROM maven:3.9.9-eclipse-temurin-21 AS service

WORKDIR /src

# The telemetry library first, into the local repository, so the service's own build
# resolves it without reaching for a registry.
COPY sdk/java/pom.xml ./telemetry/pom.xml
RUN mvn -q -f telemetry/pom.xml -DskipTests dependency:go-offline || true
COPY sdk/java/src ./telemetry/src
RUN mvn -q -f telemetry/pom.xml -DskipTests install

COPY targets/admin/pom.xml ./console/pom.xml
RUN mvn -q -f console/pom.xml -DskipTests dependency:go-offline || true
COPY targets/admin/src ./console/src
COPY --from=web /build/dist/meridian/browser ./console/src/main/resources/static
RUN mvn -q -f console/pom.xml -DskipTests package

# ----------------------------------------------------------------------- runtime
FROM eclipse-temurin:21.0.6_7-jre-jammy

# The database client and the directory tools are here because the operations handbook's
# restore procedure runs from inside this container rather than from a workstation that
# would need a route to the estate.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      mysql-client ldap-utils openssl ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system meridian && useradd --system --gid meridian --home /opt/meridian meridian

WORKDIR /opt/meridian

COPY --from=service /src/console/target/meridian-console.jar /opt/meridian/console.jar
COPY targets/admin/db /opt/meridian/db
COPY targets/admin/directory /opt/meridian/directory
COPY targets/admin/data /opt/meridian/data
COPY targets/admin/state-reset /usr/local/bin/state-reset

# The pristine copy the restore procedure re-derives the settlement keys from.
RUN cp /opt/meridian/data/tariffs.xml /opt/meridian/data/tariffs.xml.seeded \
 && chmod 0755 /usr/local/bin/state-reset \
 && mkdir -p /var/lib/meridian/imports /var/log/meridian \
 && chown -R meridian:meridian /opt/meridian /var/lib/meridian /var/log/meridian

USER meridian
EXPOSE 8080

# Heap is small on purpose: this console runs beside a dozen others on the same host and
# the sizing came out of the March review. The extension setting is here because two
# clients' statement layouts call functions the strict profile refuses, and the export
# screen is the only thing that compiles them.
ENV JAVA_TOOL_OPTIONS="-Xms128m -Xmx512m -XX:+UseSerialGC -Djdk.xml.enableExtensionFunctions=true -Djava.security.egd=file:/dev/urandom"

HEALTHCHECK --interval=10s --timeout=4s --retries=30 --start-period=45s \
  CMD curl -fsS http://127.0.0.1:8080/actuator/health || exit 1

ENTRYPOINT ["java", "-jar", "/opt/meridian/console.jar"]
