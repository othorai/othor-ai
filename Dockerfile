# SPDX-License-Identifier: Apache-2.0
# One image, four roles chosen by command: web, worker, cli (docs/02).

# Which image (M20.4): core → othor (the open core), ee → othor-ee (+ the licence-gated
# enterprise tree), hosted → othor.ai (+ the private website). The source stage copies the build
# context and trims it to the edition (scripts/edition.py); later stages copy only what's left,
# so no layer of the othor image ever holds the private code.
ARG EDITION=core

FROM python:3.12.16-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS src
ARG EDITION
WORKDIR /src
COPY . .
RUN python scripts/edition.py "$EDITION" && mkdir -p othor_site othor_ee

# The stylesheets, built once here with the pinned, checksum-verified Tailwind standalone CLI
# (the app's, and the website's when it's in the edition). No Node; images ship the result.
FROM python:3.12.16-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS css
ARG TARGETARCH
WORKDIR /src
COPY --from=src /src/scripts/build_css.py scripts/build_css.py
COPY --from=src /src/othor/web othor/web
COPY --from=src /src/othor_site othor_site
# the Enterprise Edition's templates (empty in the open core): app.css scans them for utilities
COPY --from=src /src/othor_ee othor_ee
RUN python scripts/build_css.py --platform "linux-$([ "$TARGETARCH" = arm64 ] && echo arm64 || echo x64)" \
 && python scripts/build_css.py --check --platform "linux-$([ "$TARGETARCH" = arm64 ] && echo arm64 || echo x64)" \
 && mkdir -p othor_site/static/site/css

# Licences: the build fails if a shipped package or asset isn't allowed (docs/licences); the
# image ships THIRD_PARTY_LICENSES with every licence text, served at /licences.
FROM python:3.12.16-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS licences
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
WORKDIR /src
COPY --from=src /src/pyproject.toml /src/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && uv pip install pyyaml==6.0.2 packaging==26.3
COPY --from=src /src/scripts/check_licences.py scripts/check_licences.py
COPY --from=src /src/docs/licences docs/licences
RUN uv run --no-sync python scripts/check_licences.py --write THIRD_PARTY_LICENSES

FROM python:3.12.16-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

# pg_dump for the nightly backup (M11b §5; on-premise uses it too). Debian's client is newer than
# the server (PostgreSQL 16), which pg_dump needs. Pango for WeasyPrint, the brief's PDF (M15.7):
# text layout only; fonts are the bundled ones, never the system's.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client libpango-1.0-0 libpangoft2-1.0-0 \
 && rm -rf /var/lib/apt/lists/* \
 && pg_dump --version

# UV_NO_CACHE: no download cache left in the image's layers (14 Nov 2026: it doubled the
# dependencies' size and filled the instance's disk on a deploy)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 OTHOR_DATA_DIR=/data
WORKDIR /app

COPY --from=src /src/pyproject.toml /src/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# the edition's packages only (M20.4): othor always; othor_ee and othor_site when it ships them
# (empty folders otherwise, removed below); their entry points come with `uv sync`
COPY --from=src /src/othor ./othor
COPY --from=src /src/othor_site ./othor_site
COPY --from=src /src/othor_ee ./othor_ee
COPY --from=css /src/othor/web/static/css/ ./othor/web/static/css/
COPY --from=css /src/othor_site/static/site/css/ ./othor_site/static/site/css/
RUN find othor_site othor_ee -type d -empty -delete 2>/dev/null; true
# The image never ships without its stylesheet (M12b): the manifest and the file it names exist.
RUN python -c "import json, pathlib, sys; d = pathlib.Path('othor/web/static'); \
rel = json.loads((d / 'css/manifest.json').read_text())['app.css']; f = d / rel; \
sys.exit(0 if f.is_file() and f.stat().st_size > 0 and 'dev' not in f.name else 'stylesheet missing')"
COPY --from=licences /src/THIRD_PARTY_LICENSES ./THIRD_PARTY_LICENSES
RUN uv sync --frozen --no-dev

ARG EDITION
# which image this is (/healthz shows it); the open images run one company per install
ENV OTHOR_EDITION=$EDITION
LABEL org.opencontainers.image.title="Othor AI" \
      org.opencontainers.image.source="https://github.com/othorai/othor-ai" \
      org.opencontainers.image.vendor="Othor AI Private Limited" \
      ai.othor.edition=$EDITION
RUN useradd --uid 10001 --create-home othor && mkdir -p /data && chown othor /data
USER othor
VOLUME ["/data"]
EXPOSE 8000
# The web role's health (the load balancer checks the same path). The worker's container turns
# this off; its heartbeat is /healthz/worker (M11b §6).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
ENTRYPOINT ["othor"]
CMD ["web", "--host", "0.0.0.0", "--port", "8000"]
