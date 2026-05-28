FROM --platform=linux/amd64 ghcr.io/laude-institute/t-bench/python-3-13:20250620

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/opt/jdk7
ENV PATH="$JAVA_HOME/bin:$PATH"

ARG ZULU7_URL=https://cdn.azul.com/zulu/bin/zulu7.56.0.11-ca-jdk7.0.352-linux_x64.tar.gz
ARG ZULU7_SHA256=8a7387c1ed151474301b6553c6046f865dc6c1e1890bcf106acc2780c55727c8
RUN mkdir -p /opt/zulu7 \
 && curl -fsSL "$ZULU7_URL" -o /tmp/zulu7.tgz \
 && echo "$ZULU7_SHA256  /tmp/zulu7.tgz" | sha256sum -c - \
 && tar -xzf /tmp/zulu7.tgz -C /opt/zulu7 \
 && rm /tmp/zulu7.tgz \
 && JDK_DIR="$(find /opt/zulu7 -mindepth 1 -maxdepth 1 -type d -name 'zulu7*' | head -n 1)" \
 && test -n "$JDK_DIR" \
 && rm -rf /opt/jdk7 \
 && ln -s "$JDK_DIR" /opt/jdk7 \
 && ln -sf /opt/jdk7/bin/java /usr/local/bin/java \
 && ln -sf /opt/jdk7/bin/javac /usr/local/bin/javac \
 && ln -sf /opt/jdk7/bin/jar /usr/local/bin/jar

RUN pip install --no-cache-dir pytest==8.4.2

WORKDIR /app

RUN mkdir -p /opt/legacy-lib \
 && curl -fsSL -o /opt/legacy-lib/h2-1.3.176.jar https://repo1.maven.org/maven2/com/h2database/h2/1.3.176/h2-1.3.176.jar \
 && true

COPY data ./data

CMD ["bash"]