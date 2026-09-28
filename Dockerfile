FROM python:3.13-slim

WORKDIR /app

# video.py's _probe (and the mp4s it produces) need ffprobe/ffmpeg on the box — neither
# ships with python:3.13-slim. Before the pip layer: an apt layer changes far less often
# than requirements.txt, so putting pip first would invalidate this every dependency bump.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

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
RUN mkdir -p out/uploads out/shoots out/videos

ENV PORT=8080 PROVIDER=fal PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
