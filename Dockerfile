FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y curl \
    && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt ./
COPY ./app /app

RUN pip install -r requirements.txt

WORKDIR /app

ENTRYPOINT ["python"]

CMD ["-m", "app.main"]
