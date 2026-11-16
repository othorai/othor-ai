# Changelog

Notable changes for people running Othor AI, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the product's own
(the same number as the images and `/healthz`).

## [Unreleased]

## [0.20.0] - 2026-11-16

Othor AI has run as a hosted service since 0.1; this release makes the whole product something
you can run yourself.

### Added
- Self-hosting: the `othor` image, `compose.selfhost.yml` with Caddy for HTTPS (Let's Encrypt,
  or Caddy's own certificate authority or your certificate on an internal network), PostgreSQL
  16, a one-shot database migration, a backup script and [the guide](docs/self-host.md). One
  company per install.
- System Health: one button on Home opens every light, from the source databases through the
  night's run to the morning's briefs, each coloured by urgency, with a nightly replay check.
- What's ahead cards in every brief: each headline number's range ahead, with its line chart and
  a plain confidence word.
- Enterprise Edition: the `othor-ee` image, with several companies on one install under an offline
  licence key.

### Changed
- A brief's title leads with what changed since the previous one; the archive keeps one row per
  brief and day.
- Every time is shown in 12-hour AM/PM with the time zone.
- Sweep states its false-discovery control in plain words ("at most 1 in 100 could be chance");
  the rate and method sit behind an (i).
- The rising-variance chart labels its band "middle 80%".
- Emails and pages name the install's own support address, or the person who runs it.
