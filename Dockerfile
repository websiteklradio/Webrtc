FROM python:3.11-slim

WORKDIR /app

COPY signaling/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY signaling/ .

CMD ["uvicorn", "signaling:app", "--host", "0.0.0.0", "--port", "8000"]