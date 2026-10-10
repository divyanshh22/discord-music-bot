FROM eclipse-temurin:17-jre-jammy

ARG LAVALINK_VERSION=4.2.2
ARG LAVALINK_SHA256=8cb801e591072c3689fafd71ccf571a95a4ead3cc35dfc045e157d763d89119a
WORKDIR /opt/Lavalink

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://github.com/lavalink-devs/Lavalink/releases/download/${LAVALINK_VERSION}/Lavalink.jar" -o Lavalink.jar \
    && echo "${LAVALINK_SHA256}  Lavalink.jar" | sha256sum -c - \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY lavalink/application.yml /opt/Lavalink/application.yml

EXPOSE 2333
ENTRYPOINT ["java", "-jar", "/opt/Lavalink/Lavalink.jar"]
