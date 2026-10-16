FROM python:3.12-slim
ADD --checksum=sha256:23074863ece39c6258ca439381e7da385bdbed6104f9bdf4e663fe98a52e6887 https://github.com/keploy/keploy/releases/download/v3.6.126/keploy_linux_amd64.tar.gz /tmp/keploy.tar.gz
RUN tar -xzf /tmp/keploy.tar.gz -C /tmp \
    && install /tmp/keploy /usr/local/bin/keploy \
    && rm /tmp/keploy.tar.gz /tmp/keploy
WORKDIR /service
COPY workers/integration_keploy/requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app /service/app
COPY workers /service/workers
ENV PYTHONPATH=/service KEPLOY_DISABLE_TELEMETRY=true
EXPOSE 8102
CMD ["uvicorn", "workers.integration_keploy.service:app", "--host", "0.0.0.0", "--port", "8102"]
