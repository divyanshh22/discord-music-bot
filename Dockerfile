FROM python:3.11-slim-bookworm

ARG LAVALINK_VERSION=4.2.2
ARG LAVALINK_SHA256=8cb801e591072c3689fafd71ccf571a95a4ead3cc35dfc045e157d763d89119a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JAVA_TOOL_OPTIONS="-XX:+UseSerialGC -Xms64m -Xmx192m -XX:MaxMetaspaceSize=96m -XX:MaxDirectMemorySize=32m -Xss512k"

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless ca-certificates curl bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app
RUN curl -fsSL "https://github.com/lavalink-devs/Lavalink/releases/download/${LAVALINK_VERSION}/Lavalink.jar" -o /app/Lavalink.jar \
    && echo "${LAVALINK_SHA256}  /app/Lavalink.jar" | sha256sum -c -

CMD ["bash", "/app/render-entrypoint.sh"]
