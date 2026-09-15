# RELEASE.2025-10-15T17-29-55Z fixes CVE-2025-62506. Upstream publishes source only.
# Provenance and upgrade procedure: docs/audit/minio-image-2026-09-15.md.
FROM golang:1.24.8-bookworm AS build

ENV CGO_ENABLED=0 \
    GOTOOLCHAIN=local \
    GOFLAGS=-mod=readonly \
    GOMAXPROCS=1 \
    GOMEMLIMIT=512MiB

WORKDIR /src/minio

# The commit is deliberately not an overridable build argument or a floating ref.
RUN git init . \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth 1 origin 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a \
    && test "$(sed -n 's/^toolchain //p' go.mod)" = go1.24.8

RUN go mod download && go mod verify

# Use upstream's build flags and version generator, with a stable release timestamp.
# Compile one package at a time to leave headroom for the live app on small VPSs.
# GOMEMLIMIT is a per-process soft target, not a cap on the entire Docker build.
RUN go build -p 1 -tags kqueue -trimpath \
    --ldflags "$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go 2025-10-15T17:29:55Z)" \
    -o /out/minio .

FROM debian:bookworm-slim

LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
      org.opencontainers.image.version="RELEASE.2025-10-15T17-29-55Z" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /out/minio /usr/bin/minio
COPY --from=build /src/minio/dockerscripts/docker-entrypoint.sh /usr/bin/docker-entrypoint.sh
COPY --from=build /src/minio/LICENSE /usr/share/licenses/minio/LICENSE
RUN chmod 0755 /usr/bin/minio /usr/bin/docker-entrypoint.sh \
    && minio --version

EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["minio", "server", "/data", "--console-address", ":9001"]
