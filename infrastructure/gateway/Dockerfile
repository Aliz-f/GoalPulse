FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && addgroup --system goalpulse \
    && adduser --system --ingroup goalpulse --home /nonexistent goalpulse
COPY --chown=goalpulse:goalpulse goalpulse_gateway ./goalpulse_gateway

USER goalpulse
EXPOSE 8000
CMD ["uvicorn", "goalpulse_gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
