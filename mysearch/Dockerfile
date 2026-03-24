FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /tmp/mysearch-requirements.txt
RUN pip install --no-cache-dir -r /tmp/mysearch-requirements.txt

COPY . /app/mysearch
RUN chmod +x /app/mysearch/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/mysearch/docker-entrypoint.sh"]
CMD ["python", "-m", "mysearch", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
