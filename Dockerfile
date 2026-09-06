FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Pinned, resolved dependency set (see requirements.lock); regenerate with
# scripts/lock.sh when requirements.txt changes.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

# Run as an unprivileged user. docker-entrypoint.py starts as root only to
# hand the /data volume to this user, then drops privileges.
RUN useradd --system --create-home --uid 10001 ezy \
    && chown -R ezy:ezy /app

EXPOSE 8080

ENTRYPOINT ["python", "docker-entrypoint.py"]
CMD ["python", "main.py"]
