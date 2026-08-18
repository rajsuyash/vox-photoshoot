FROM python:3.13-slim

WORKDIR /app

# Dependencies first so code edits do not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY static/ static/
# The cast portraits and location plates are the product: without them the pickers are
# empty and every shoot loses its face reference.
COPY assets/ assets/

# Generated output is ephemeral on App Runner. Fine for a demo; a persistent deployment
# should write to S3 instead — see README.
RUN mkdir -p out/uploads out/shoots

ENV PORT=8080 PROVIDER=fal PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
