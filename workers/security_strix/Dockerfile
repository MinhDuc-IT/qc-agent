FROM python:3.12-slim
WORKDIR /service
COPY workers/security_strix/requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app /service/app
COPY workers /service/workers
ENV PYTHONPATH=/service STRIX_TELEMETRY=0
EXPOSE 8103
# Strix starts its own sandbox container. Deploy this only on a dedicated worker
# and mount the Docker socket; it is intentionally not part of PR execution.
CMD ["uvicorn", "workers.security_strix.service:app", "--host", "0.0.0.0", "--port", "8103"]
