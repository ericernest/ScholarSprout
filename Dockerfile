FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 --user-group novicesynapse \
    && mkdir -p /var/lib/novicesynapse/audit \
    && chown -R novicesynapse:novicesynapse /var/lib/novicesynapse /home/novicesynapse

USER novicesynapse
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)" || exit 1

CMD ["novicesynapse", "gateway", "--host", "0.0.0.0", "--port", "8000"]
