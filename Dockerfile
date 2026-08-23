FROM python:3.13-slim

WORKDIR /app

# Dependencies first so code edits do not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY static/ static/
# db.migrate() runs on boot and reads these off disk. They were never copied in, so for
# every deploy up to this one it globbed a directory that did not exist, found nothing,
# and reported success — the schema was only ever whatever had been applied to RDS by
# hand. Shipping a table the running image did not know how to create is how that was
# finally noticed. db.migrate() now refuses to boot without this directory.
COPY migrations/ migrations/
# The cast portraits and location plates are the product: without them the pickers are
# empty and every shoot loses its face reference.
COPY assets/ assets/

# Generated output is ephemeral on App Runner. Fine for a demo; a persistent deployment
# should write to S3 instead — see README.
RUN mkdir -p out/uploads out/shoots

ENV PORT=8080 PROVIDER=fal PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
