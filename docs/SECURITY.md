# Security notes

DeepSeek API Proxy & Token Monitor is intended for local or explicitly trusted
network use. Read this page before enabling remote access.

## Network exposure

- The proxy binds to 127.0.0.1 by default.
- The proxy and dashboard have **no built-in authentication or authorization**.
- The dashboard exposes usage data and write endpoints for labels and pricing.
- Do not bind to 0.0.0.0 or a LAN address on an untrusted network. Do not expose
  the service directly to the Internet.
- For remote use, put it behind an authenticated access layer and TLS, restrict
  network reachability with a firewall, and ensure only trusted users can reach
  both the message proxy and dashboard.

## API keys and stored data

The proxy forwards the API key supplied by the client in the x-api-key header.
It does not need a separate DeepSeek API key to proxy these requests. The local
usage database stores a one-way fingerprint of that key, not its plaintext.
The monitor application can separately store the API key used for account
balance queries; protect that configuration directory and any backups.

Usage records include timestamps, model names, token counts, request IDs, and
the key fingerprint. If InfluxDB is configured, these records are sent to that
database as well. Restrict access to both the SQLite file and InfluxDB.

## Client request bodies

The proxy streams request bodies to DeepSeek; it does not intentionally persist
prompt or response content. Clients should still be configured with the model
and endpoint they intend to use, and network access should be treated as access
to the configured DeepSeek account.

## Reporting a security issue

Please avoid posting credentials, API keys, or private usage data in public
issues. Redact sensitive information from logs and provide a minimal
reproducible report.
