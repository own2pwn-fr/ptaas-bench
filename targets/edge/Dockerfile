# Stdlib-only build, so there is nothing to download and the image is reproducible.
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY origin/go.mod ./
COPY origin/*.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/origin .

FROM alpine:3.20
RUN adduser -D -u 10001 app
COPY --from=build /out/origin /usr/local/bin/origin
COPY state-reset /usr/local/bin/state-reset
RUN chmod 0755 /usr/local/bin/state-reset
USER app
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/origin"]
