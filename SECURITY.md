# Security

## Reporting a vulnerability

Please email **security@othor.ai** with what you found and how to reproduce it. Don't open a
public issue. We reply within 3 working days, keep you updated, and credit you in the release
notes if you'd like.

In scope: this repository's code and its release images (`ghcr.io/othorai/othor`,
`ghcr.io/othorai/othor-ee`). The hosted service at othor.ai: also by email; please don't test
against other customers' data, run denial-of-service tests or social-engineer our people.

## Supported versions

Fixes go into the latest release. Self-hosters upgrade by changing `OTHOR_VERSION`
(see [docs/self-host.md](docs/self-host.md)).

## How Othor AI is built to be safe

Sources are read with read-only logins (a login that can write is refused), personal columns
are never read, no raw rows reach a model, source passwords are encrypted with a key that never
leaves the install, and nothing calls home.
