# ADR-0017: Route Telegram traffic through an isolated VLESS proxy

## Status

Superseded by ADR-0052.

## Context

The production VPS cannot establish a direct connection to `api.telegram.org`, which prevents the
long-polling gateway from completing its Telegram Bot API startup. Routing the whole host through
a third-party VLESS location would unnecessarily include database, deployment and legal-service
traffic.

## Decision

- Run a digest-pinned official `sing-box` image as `telegram-vpn-proxy`.
- Store the VLESS/Reality configuration exclusively at
  `/etc/dental-legal-ai/telegram-vpn/config.json`, owned by root with mode `0600`; it is not a Git
  artifact, GitHub secret or application environment variable.
- Expose the proxy only on the internal Compose `backend` network. It also joins `edge` solely to
  establish the selected VLESS connection.
- Configure both Telegram Bot API requests and long-polling updates to use the internal HTTP
  proxy. The Telegram gateway does not join `edge`, so it cannot bypass that route.
- Use a low-memory, read-only container with all Linux capabilities dropped. The production
  overlay reserves at most 96 MiB for this service.

## Consequences

- A failed VLESS configuration blocks the Telegram gateway at startup rather than silently
  reverting to direct egress.
- The rest of the application retains direct production networking and is not exposed to the VPN
  provider.
- Rotating the VPN location is a root-only server operation followed by a normal deployment or
  container restart; it does not require a source-code change.
