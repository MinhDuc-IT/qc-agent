FROM python:3.12-slim

WORKDIR /service
COPY workers/functional_hercules/requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium
COPY app /service/app
COPY workers /service/workers

ENV PYTHONPATH=/service \
    HEADLESS=true \
    AUTO_MODE=1 \
    ENABLE_TELEMETRY=0
EXPOSE 8101
CMD ["uvicorn", "workers.functional_hercules.service:app", "--host", "0.0.0.0", "--port", "8101"]
