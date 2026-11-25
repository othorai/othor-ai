# Run Othor AI on your own server

This guide is for the person who looks after your company's servers. It takes about fifteen
minutes. At the end, Othor AI runs on your server at an address like
`https://othor.yourcompany.com`, reads your databases where they are, and emails the Morning
Brief every day at 06:00.

One install runs one company. For a second company, make a second install (or ask us about the
enterprise licence, which runs several companies on one install).

## What you need

- A Linux server with **2 CPUs, 4 GB of memory and 40 GB of disk** (more disk if you keep many
  months of results). Ubuntu 24.04 is what we test on.
- **Docker** with the Compose plugin (`docker compose version` prints 2.24 or newer).
- An **address** for it, either:
  - a public domain name (`othor.yourcompany.com`) whose DNS points at the server, with ports
    **80 and 443** open to the internet, so Let's Encrypt can issue the certificate; or
  - a name only your network reaches (see [A server only your network reaches](#a-server-only-your-network-reaches)).
- An **SMTP server** for email (your mail provider's, or your own).
- For each database you connect: a **read-only** login. Othor AI refuses a login that can write.

## Install

1. Get the files (the compose file, the Caddy files and the backup script):

   ```sh
   git clone --depth 1 https://github.com/othorai/othor-ai.git othor && cd othor
   ```

2. Make your settings file and fill it in:

   ```sh
   cp .env.selfhost.example .env
   chmod 600 .env
   openssl rand -hex 32   # paste as OTHOR_SECRET_KEY
   openssl rand -hex 32   # paste as POSTGRES_PASSWORD
   ```

   Set at least `OTHOR_DOMAIN`, `OTHOR_SECRET_KEY`, `POSTGRES_PASSWORD` and `OTHOR_SMTP_URL`.
   Every setting is explained in the file.

3. Check the address points here before starting (Let's Encrypt needs it):

   ```sh
   dig +short othor.yourcompany.com   # prints this server's public IP
   ```

4. Start it:

   ```sh
   docker compose -f compose.selfhost.yml up -d
   docker compose -f compose.selfhost.yml ps    # web, worker, db and caddy are running
   ```

   The first start creates the database tables and the **instance key** (it encrypts the
   passwords of the sources you connect; it lives in the data volume, see Backups).

5. Open `https://othor.yourcompany.com` and sign up. The first person to sign up creates the
   company and becomes its owner. After that, sign-up closes. Others get the Morning Brief as its
   recipients (Morning Brief › Recipients).

## Connect your data

In Othor AI, go to **Data sources** and add PostgreSQL, MySQL or Google Sheets. Give it a
read-only login, for example on PostgreSQL:

```sql
CREATE ROLE othor_reader LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE sales TO othor_reader;
GRANT USAGE ON SCHEMA public TO othor_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO othor_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO othor_reader;
```

If the database is on another server, allow connections from this server's address.

Google Sheets needs your own Google Cloud OAuth client (set `OTHOR_SHEETS_MODE=google` and the
two `OTHOR_GOOGLE_…` settings).

## Email

Set `OTHOR_SMTP_URL`:

- port 587: `smtp+starttls://user:password@smtp.yourcompany.com:587`
- port 465: `smtps://user:password@smtp.yourcompany.com:465`

Emails come from `brief@<OTHOR_MAIL_FROM_DOMAIN>` (your domain by default). Add that domain to
your mail provider's SPF and DKIM so briefs don't land in spam.

## A model (optional)

Othor AI works without one: every number is computed by code, and the brief's sentences come
from templates. A model only drafts setup suggestions and the brief's prose. To use one, set
`OTHOR_MODEL_PROVIDER=anthropic`, `OTHOR_MODEL_ID` and `OTHOR_MODEL_API_KEY` (your own key; you
pay Anthropic directly, and Anthropic is then your provider, not ours). Only computed text goes
to it (the brief's numbers and findings as sentences, metric names): no rows from your databases
and no personal columns, ever. If it's unreachable, Othor AI falls back to templates by itself.

### A local model with Ollama

To keep even the drafting inside your network, run a model with [Ollama](https://ollama.com) and
point Othor AI at it. No key and no outside service:

```
OTHOR_MODEL_PROVIDER=ollama
OTHOR_MODEL_ID=qwen3:30b-a3b          # or gpt-oss:20b; whatever `ollama list` shows
OTHOR_MODEL_BASE_URL=http://ollama:11434
OTHOR_MODEL_CONTEXT=32768             # tokens; setup drafts need a long context
OTHOR_MODEL_THINK=                    # off · low · medium · high (blank: the model's default)
```

- **On Linux**, run Ollama beside the image (`docker run -d --gpus=all -v ollama:/root/.ollama
  -p 11434:11434 --name ollama ollama/ollama`, on the same Docker network as Othor AI), then
  `docker exec ollama ollama pull qwen3:30b-a3b`. A GPU with 24 GB or more keeps drafts quick;
  on CPU only they still arrive, slowly.
- **On a Mac**, install the Ollama app and run it natively: Docker on a Mac can't use the GPU.
  From the container, reach it at `OTHOR_MODEL_BASE_URL=http://host.docker.internal:11434`.

Only that host is let through the outbound client. The same rules apply as for any model: names
and computed results only, every number in a draft checked against the computed ones, and the
nightly run never waits for it. A local model can't read PDFs; an uploaded PDF is then read from its
text layer only.

### Any other model (0.37)

Any OpenAI-compatible API works through one provider with presets:

```
OTHOR_MODEL_PROVIDER=openai_compat
OTHOR_MODEL_PRESET=openai     # gemini · azure · mistral · deepseek · groq · together · openrouter · local
OTHOR_MODEL_ID=gpt-5
OTHOR_MODEL_API_KEY=…         # not needed for `local`
OTHOR_MODEL_BASE_URL=…        # only for azure (https://your-resource.openai.azure.com) and local
                              # (LM Studio, vLLM, llama.cpp: http://host:port/v1)
```

Or set it in the app: **Settings › AI Model** (super admin and admins) picks a provider and model
for every kind of work, or one each for *Understanding your business*, *Writing your reports* and
*Answering you*, with the key pasted there (stored encrypted with the instance key, never shown
again) and **Check this model** to try it with one short call on made-up figures. A choice made in
Settings is used before the env setting; *Back to the usual model* returns to env. Only the chosen
provider's host is let through the outbound client.

## A server only your network reaches

Let's Encrypt can't reach a server that isn't on the internet. Set `CADDYFILE=Caddyfile.internal`
in `.env`, then either:

- **Caddy's own certificate authority** (the default in that file). After the first start, copy
  its root certificate and install it on your people's computers once:

  ```sh
  docker compose -f compose.selfhost.yml cp caddy:/data/caddy/pki/authorities/local/root.crt ./othor-root.crt
  ```

- **Your own certificate**: put `cert.pem` and `key.pem` in `./certs` (or set `OTHOR_CERTS_DIR`)
  and, in `deploy/selfhost/Caddyfile.internal`, change `tls internal` to
  `tls /certs/cert.pem /certs/key.pem`.

Then `docker compose -f compose.selfhost.yml up -d` again.

## Backups

Two things hold everything: the **database** and the **data volume** (results, files and the
instance key). Back up both, every day, to somewhere other than this server:

```sh
scripts/selfhost/backup.sh /mnt/backups      # or any folder; default ./backups
```

It writes `othor-<time>/db.dump` and `othor-<time>/data.tar.gz`. Copy them off the server (a
bucket, another machine). A cron line for 01:00 every day:

```
0 1 * * * cd /opt/othor && scripts/selfhost/backup.sh /mnt/backups >> /var/log/othor-backup.log 2>&1
```

The data volume holds the instance key, which decrypts your sources' passwords: keep backups as
safe as a password.

**Restore** (onto a fresh install with the same `.env`):

```sh
docker compose -f compose.selfhost.yml up -d db
docker compose -f compose.selfhost.yml exec -T db pg_restore -U othor -d othor --clean --if-exists < db.dump
docker compose -f compose.selfhost.yml run --rm --no-deps -T --entrypoint tar web -xzf - -C /data < data.tar.gz
docker compose -f compose.selfhost.yml up -d
```

If the data volume is lost but the database is not, Othor AI won't make a new instance key on
its own (your sources' passwords couldn't be read with it): restore `data.tar.gz` first.

## Upgrades

Change `OTHOR_VERSION` in `.env` to the new release, then:

```sh
docker compose -f compose.selfhost.yml pull
docker compose -f compose.selfhost.yml up -d
```

The database is brought up to date on start. Take a backup first. Release notes are on the
GitHub releases page.

## The enterprise image

`ghcr.io/othorai/othor-ee` is the open image plus the enterprise features (several companies on
one install, and more). It needs a licence key: set `OTHOR_IMAGE=ghcr.io/othorai/othor-ee`, log
in to the registry with the read token that came with your licence
(`docker login ghcr.io`), and paste the key in **Settings › Licence** (or set `OTHOR_LICENCE`).
Without a valid key it runs exactly like the open image. Nothing is ever locked or deleted when
a licence ends.

## Check it's healthy

- `https://othor.yourcompany.com/healthz` shows `"status":"ok"`, the version and the edition.
- In Othor AI, **System Health** (next to Big Picture on Home) shows the night's run, the
  sources and the briefs.
- Logs: `docker compose -f compose.selfhost.yml logs -f web worker`.

## When something's wrong

| What you see | What to do |
| --- | --- |
| The browser says the certificate is invalid | DNS doesn't point here yet, or port 80/443 is closed. Check `dig`, the firewall, then `docker compose … logs caddy`. |
| `migrate` exits with "the instance key is missing but companies exist" | The data volume was lost. Restore `data.tar.gz` (Backups). |
| No emails | Check `OTHOR_SMTP_URL`; `docker compose … logs worker` shows the relay's answer. |
| A source is refused as "can write" | Give Othor AI a login with SELECT only. |
| The night didn't run | `docker compose … ps` shows the worker running; System Health says which step stopped. |

Questions and bugs: GitHub issues on `othorai/othor-ai`. Security reports: see `SECURITY.md`.
