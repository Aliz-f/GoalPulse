FROM python:3.12-slim AS runtime

ARG SERVICE_PATH
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace
COPY ${SERVICE_PATH}/requirements.txt /tmp/service-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/service-requirements.txt \
    && addgroup --system goalpulse \
    && adduser --system --ingroup goalpulse --home /nonexistent goalpulse
COPY --chown=goalpulse:goalpulse contracts ./contracts
COPY --chown=goalpulse:goalpulse ${SERVICE_PATH} ./${SERVICE_PATH}

USER goalpulse
