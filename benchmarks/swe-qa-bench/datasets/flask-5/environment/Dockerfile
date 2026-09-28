FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN git init /app \
    && git -C /app remote add origin https://github.com/pallets/flask.git \
    && git -C /app fetch --depth=1 origin 85c5d93cbd049c4bd0679c36fd1ddcae8c37b642 \
    && git -C /app checkout --detach FETCH_HEAD \
    && test "$(git -C /app rev-parse HEAD)" = "85c5d93cbd049c4bd0679c36fd1ddcae8c37b642" \
    && git -C /app remote remove origin

WORKDIR /app
