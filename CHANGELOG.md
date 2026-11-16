# Changelog

Notable changes for people running Othor AI, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the product's own
(the same number as the images and `/healthz`).

## [Unreleased]

The first public release. Othor AI has been built and run as a hosted service since 0.1;
this is the first version published as open source.

### Added
- Self-hosting: the `othor` image, `compose.selfhost.yml` with Caddy for HTTPS (Let's Encrypt,
  or your own certificate on an internal network), PostgreSQL, a backup script and
  [the guide](docs/self-host.md). One company per install.
- Enterprise: the `othor-ee` image and an offline licence key; several companies on one
  install is the first enterprise feature.
