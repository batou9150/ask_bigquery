FROM python:3.11-slim-buster

COPY requirements.txt ./

RUN pip install -r requirements.txt

COPY ask_bigquery /app

CMD ["streamlit", "run", "/app/main.py", "--server.address=0.0.0.0"]