FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git python3 libgomp1 libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY source/ /workspace/
WORKDIR /workspace
