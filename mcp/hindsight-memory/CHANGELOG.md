# Changelog

## 1.0.0 — 2026-11-05

- Add explicit-project-bank stdio access to the existing Hindsight 1.2.0 service.
- Add optional caller operation UUIDs to MCP `retain`, using native async REST replay without changing ordinary MCP calls.
- Add a one-request, loopback-only API relay with native response and UNKNOWN-state preservation; the VPS owns bounded retries.
- Add a Mac LAN SSH launcher reading the ignored repository `.env` and publish client/deployment contracts.

Source, private deployment, native client adoption and engineering acceptance remain separate records under Issue #13.
