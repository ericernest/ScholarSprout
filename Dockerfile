FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 --user-group scholarsprout \
    && mkdir -p /var/lib/scholarsprout/audit \
    && chown -R scholarsprout:scholarsprout /var/lib/scholarsprout /home/scholarsprout

USER scholarsprout
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)" || exit 1

CMD ["scholarsprout", "gateway", "--host", "0.0.0.0", "--port", "8000"]
