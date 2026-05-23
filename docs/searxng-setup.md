# SearxNG Setup for PAL

PAL's chat-driven research path (`propose_research` / `research_topic`) requires a running SearxNG instance for web search. SearxNG is a self-hosted meta-search engine that aggregates results from multiple sources without tracking.

## Recommended: Docker on the inference server

### 1. Create a working directory

```bash
mkdir -p ~/searxng
cd ~/searxng
```

### 2. Create `settings.yml`

```yaml
use_default_settings: true

general:
  instance_name: "PAL Search"

server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "CHANGE_ME_TO_A_RANDOM_STRING"
  limiter: false
  image_proxy: false

search:
  safe_search: 0
  formats:
    - html
    - json

engines:
  - name: wikipedia
    disabled: false
  - name: arxiv
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: github
    disabled: false
  - name: stackoverflow
    disabled: false
```

Generate a secret key:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Paste the output as the `secret_key` value.

### 3. Run the container

```bash
docker run -d \
  --name searxng \
  --restart unless-stopped \
  -p 8080:8080 \
  -v $(pwd)/settings.yml:/etc/searxng/settings.yml \
  searxng/searxng:latest
```

### 4. Verify it works

```bash
curl "http://localhost:8080/search?q=python&format=json" | head -c 200
```

You should see JSON output including `"results": [...]`.

### 5. Configure PAL

Set the env var when running the daemon:
```bash
export PAL_SEARXNG_URL=http://192.168.1.14:8080
```

(Use `localhost` instead of the IP if the daemon runs on the same machine as SearxNG.)

## Alternative: Direct Python install

If you'd rather not use Docker:

```bash
git clone https://github.com/searxng/searxng.git
cd searxng
python3 -m venv searxng-pyenv
source searxng-pyenv/bin/activate
pip install -e .
python -m searx.webapp
```

Then set `PAL_SEARXNG_URL` the same way.

## Troubleshooting

**"Connection refused"** — Container isn't running. `docker ps` to check. `docker logs searxng` for errors.

**JSON format not enabled** — Double-check `formats: [html, json]` in `settings.yml`. SearxNG disables JSON by default in newer versions.

**All results filtered by allowlist** — Expected on first use. Edit `_config/allowlist.md` in your vault to add domains SearxNG returns that you trust.

**Slow searches** — Disable engines you don't care about in `settings.yml`. Each enabled engine adds latency.
