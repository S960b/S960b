# Needs 64-bit CPython: hash(n) == n % (2**61 - 1) for non-negative ints.
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY static/ static/

# Single worker so the in-memory graph store is shared across requests.
EXPOSE 3000
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:3000", "app:app"]
