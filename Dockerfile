FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
COPY fonts /usr/local/share/fonts/custom
RUN fc-cache -f -v

EXPOSE 8501

CMD ["streamlit", "run", "webapp/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
