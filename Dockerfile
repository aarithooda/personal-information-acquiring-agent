# PIA as one container: the web app plus the background agent. State lives in /app/data (mount a volume there).
#
#   docker build -t pia .
#   docker run -d --name pia -p 8765:8765 -v pia-data:/app/data \
#       -e PIA_PASSWORD='a long password' -e GROQ_API_KEY=... -e PIA_ALLOWED_HOSTS=pia.example.org pia
#
# PIA refuses to listen on a public address without PIA_PASSWORD. Put HTTPS in front of it.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIA_HOST=0.0.0.0 PORT=8765
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY config ./config
# Editable install: PIA finds config/ and data/ relative to its source tree (/app).
RUN pip install --no-cache-dir -e ".[web]" && useradd --create-home --uid 10001 pia && mkdir -p /app/data && chown -R pia:pia /app

USER pia
VOLUME ["/app/data"]
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/api/health',timeout=4)" || exit 1

CMD ["pia", "serve"]
