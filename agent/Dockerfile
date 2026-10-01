# BarathSeva civic triage agent — aiKart "Try Me Now" sandbox image.
#
# Build from the REPOSITORY ROOT, not from agent/, because the agent imports
# the real pipeline code out of backend/app rather than reimplementing it:
#
#   docker build -f agent/Dockerfile -t startrek/barathseva-civic-triage:1.0.0 .
#
# Two deliberate choices:
#   * No API key is baked in. This image is public and the aiKart manifest has
#     no secret-injection mechanism, so a key here would be a published
#     credential. The agent runs offline on deterministic rules unless the
#     buyer supplies their own key at run time.
#   * Only the dependencies the triage path actually uses are installed. The
#     full backend pulls FastAPI, Celery, LangGraph, SQLAlchemy and a Postgres
#     driver; none of them run here. The image-gate vocabulary lives in
#     app/ai/image_policy.py precisely so this container can apply the same
#     policy as the platform without importing the database layer.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Pinned to match backend/requirements.txt so the sandbox behaves like the
# platform it is demonstrating.
RUN pip install --no-cache-dir \
      "pydantic==2.10.4" \
      "pydantic-settings==2.7.0" \
      "Pillow==11.1.0" \
      "httpx==0.28.1"

# The pipeline code, imported as-is. The agent is a thin entrypoint over the
# same modules the production platform runs.
COPY backend/app /app/app
COPY agent/run.py agent/samples.py /app/

# aiKart mounts its own I/O here; create it so a local run works identically.
RUN mkdir -p /aikart

# Fails the build rather than the buyer's first run if an import regresses.
RUN python -c "import app.ai.prompts, app.ai.factory, app.ai.image_policy, app.core.city, app.core.exif, samples; print('import check ok')"

# Non-root: the sandbox is isolated, but a container that never needs root
# should not run as it.
RUN useradd --create-home --uid 10001 agent \
 && chown -R agent:agent /app /aikart
USER agent

CMD ["python", "run.py"]
