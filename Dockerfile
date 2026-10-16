# Keel in a container: the CLI, the evaluation and the tests, with the Flask checkout it evaluates against.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-ci.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN git clone -q https://github.com/pallets/flask.git targets/flask
ENV KEEL_DB_HOST=falkordb PYTHONUNBUFFERED=1
CMD ["python", "-m", "keel", "cases"]
