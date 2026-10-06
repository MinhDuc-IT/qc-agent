FROM ghcr.io/keploy/keploy:v3.8.58 AS keploy
FROM python:3.12-slim
COPY --from=keploy /usr/local/bin/keploy /usr/local/bin/keploy
WORKDIR /service
COPY workers/integration_keploy/requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app /service/app
COPY workers /service/workers
ENV PYTHONPATH=/service KEPLOY_DISABLE_TELEMETRY=true
EXPOSE 8102
CMD ["uvicorn", "workers.integration_keploy.service:app", "--host", "0.0.0.0", "--port", "8102"]
