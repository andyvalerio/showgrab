FROM python:3.12-slim

# SQLite lives under /config; that's the volume mount point in every
# deployment (matches SHOWGRAB_DB_PATH default in service/__main__.py).
RUN mkdir -p /config && useradd --create-home --uid 1000 showgrab && chown showgrab:showgrab /config

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

USER showgrab
EXPOSE 8989
ENV SHOWGRAB_DB_PATH=/config/showgrab.db
ENV SHOWGRAB_PORT=8989
VOLUME ["/config"]

CMD ["showgrab-serve"]
