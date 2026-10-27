FROM eclipse-temurin:8-jdk-jammy

RUN apt-get update && \
    apt-get install -y maven && \
    rm -rf /var/lib/apt/lists/*

# Mirror the host user's home so Maven uses /home/atsum/.m2 (same path as host mount).
RUN mkdir -p /home/atsum

WORKDIR /workspace
