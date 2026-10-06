FROM grafana/k6:2.3.0 AS k6
FROM python:3.12-slim
COPY --from=k6 /usr/bin/k6 /usr/local/bin/k6
WORKDIR /service
COPY workers/performance_k6/requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app /service/app
COPY workers /service/workers
ENV PYTHONPATH=/service
EXPOSE 8104
CMD ["uvicorn", "workers.performance_k6.service:app", "--host", "0.0.0.0", "--port", "8104"]
