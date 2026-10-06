# ADR-0052: Use direct Telegram egress; keep official watch opt-in

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

ADR-0017 required every Telegram Bot API request and official-publication check to pass through
an internal VLESS sidecar. On the replacement VPS, a direct TLS request reaches Telegram, while
the saved VLESS configurations cannot establish a usable proxy connection. A mandatory failed
proxy makes the bot unavailable even when its required public endpoint is reachable.

The official publication portal does not establish a direct TLS connection from this VPS within
the bounded client timeout. Its watcher is a maintenance function that stages candidates for
review; it is not required to answer bot commands or serve the already approved corpus.

## Decision

The production overlay uses direct Telegram egress by default:

- `telegram-gateway` inherits its `backend` and `edge` networks from the base Compose contract.
  The backend remains private; the edge network does not publish any gateway port.
- `TELEGRAM_PROXY_URL` defaults to an empty value, so the gateway uses HTTPS directly.
- The `legal-watcher` and `legal-watch-importer` pair belongs to the explicit
  `official-watch` profile. It is absent from routine deployments. An operator may start it only
  after independently verifying a reviewed source-specific egress route; its
  `LEGAL_WATCH_PROXY_URL` can then be set to that reviewed route.
- The production overlay no longer creates or starts a `telegram-vpn-proxy` sidecar. Existing
  root-owned VPN files are inert and are not deployment inputs.
- Direct connectivity and the Telegram token must be verified with `getMe` before the gateway is
  started. Tokens are never printed in commands or logs.

## Consequences

Routine bot deployment no longer depends on a third-party VPN subscription or its availability.
The gateway can still be configured with a reviewed HTTP proxy in a future, separately approved
network change, but reintroducing a sidecar requires a new Compose contract, security review and
tests. The maintenance watcher never approves a legal version and its absence cannot broaden
inbound exposure or enable Hermes/LLM analysis.
