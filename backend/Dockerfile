# Backend image — FastAPI + the in-process daily scheduler (§3.8).
#
# Slim rather than alpine: psycopg ships manylinux wheels that alpine's musl
# would force a source build of, and a five-minute image build to save 40MB
# is a bad trade for a tool one person runs.
FROM python:3.12-slim

# Unbuffered so `docker compose logs -f` shows a sync as it happens rather
# than when the buffer flushes — the log is the only window into a job that
# runs at 05:00 while nobody is watching.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first, so a code change does not reinstall the dependency
# tree on every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# §3.2's payload archive lives here and must outlive the container — it is
# what makes a full rebuild possible after a Flex window rolls off. Created
# with ownership before the volume mounts so the non-root user can write.
ENV SETTLED_DATA_DIR=/data
RUN useradd --create-home --uid 10001 settled \
    && mkdir -p /data \
    && chown -R settled:settled /data /app
USER settled

EXPOSE 8000

# No `--reload`: it watches the filesystem, doubles the process tree, and
# has no business in a deployment. One worker is deliberate — see the
# scheduler note in docker-compose.yml.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
