# Meridian Portal.
#
# Built from the repository root so that the internal telemetry package is in the build
# context; it is referenced by project rather than restored from the internal feed,
# which is what the release pipeline does for a service and its own libraries.
#
# Tags are exact. A floating tag would change what ships without changing anything in
# this repository, and this image is deployed from a mirror. At first bring-up, record
# `docker image inspect --format '{{index .RepoDigests 0}}'` for both and replace the
# tags with image@sha256:... digests.

FROM mcr.microsoft.com/dotnet/sdk:9.0.101-noble AS build

WORKDIR /src
COPY sdk/dotnet/Internal.Telemetry/ sdk/dotnet/Internal.Telemetry/
COPY targets/corp/ targets/corp/

RUN dotnet publish targets/corp/Portal.csproj \
        --configuration Release \
        --output /app \
        /p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:9.0.0-noble

ENV DOTNET_EnableDiagnostics=0 \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    ASPNETCORE_ENVIRONMENT=Production

# curl is the only addition: the operations script speaks to the loopback listener with
# it, and the container health check uses the same listener.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --system --no-create-home --uid 10021 portal; \
    mkdir -p /var/lib/portal/uploads /var/lib/portal/templates; \
    chown -R portal:portal /var/lib/portal

WORKDIR /app
COPY --from=build /app ./
COPY targets/corp/state-reset /usr/local/bin/state-reset
RUN chmod 0755 /usr/local/bin/state-reset

USER 10021:10021

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD curl -fsS http://127.0.0.1:9310/ops/health || exit 1

ENTRYPOINT ["dotnet", "/app/Portal.dll"]
