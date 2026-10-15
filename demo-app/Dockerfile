FROM python:3.12-slim
WORKDIR /app
COPY src/ /app/src/
ENV PYTHONPATH=/app/src PORT=8080
EXPOSE 8080
USER nobody
CMD ["python", "-m", "shortener"]
