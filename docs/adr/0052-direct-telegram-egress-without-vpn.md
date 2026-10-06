# ADR-0052: Use direct Telegram and official-publication egress by default

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

ADR-0017 required every Telegram Bot API request and official-publication check to pass through
an internal VLESS sidecar. On the replacement VPS, a direct TLS request reaches Telegram, while
the saved VLESS configurations cannot establish a usable proxy connection. A mandatory failed
proxy makes the bot unavailable even when its required public endpoint is reachable.

## Decision

The production overlay uses direct egress by default:

- `telegram-gateway` inherits its `backend` and `edge` networks from the base Compose contract.
  The backend remains private; the edge network does not publish any gateway port.
- `TELEGRAM_PROXY_URL` and `LEGAL_WATCH_PROXY_URL` default to empty values, so the gateway and
  watcher use HTTPS directly.
- The production overlay no longer creates or starts a `telegram-vpn-proxy` sidecar. Existing
  root-owned VPN files are inert and are not deployment inputs.
- Direct connectivity and the Telegram token must be verified with `getMe` before the gateway is
  started. Tokens are never printed in commands or logs.

## Consequences

Routine deployment no longer depends on a third-party VPN subscription or its availability. The
gateway can still be configured with a reviewed HTTP proxy in a future, separately approved
network change, but reintroducing a sidecar requires a new Compose contract, security review and
tests. This change does not broaden inbound exposure or enable Hermes/LLM analysis.
