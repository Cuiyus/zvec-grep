FROM node:24-bookworm-slim AS claude-install
WORKDIR /opt/qa
COPY runtime/package.json runtime/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && test "$(./node_modules/.bin/claude --version)" = "2.1.212 (Claude Code)"

FROM ubuntu:24.04 AS native-package
COPY runtime/zg-native.tgz /tmp/zg-native.tgz
RUN echo '470c2a11b8b19a9e6458c977918260ee6f535595488ed8b44233db5aa032ad4d  /tmp/zg-native.tgz' | sha256sum -c - \
    && mkdir -p /opt/zg \
    && tar -xzf /tmp/zg-native.tgz --strip-components=1 -C /opt/zg

# The current-main Rust artifact is built on Ubuntu 24.04 and needs its glibc.
FROM ubuntu:24.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git libgomp1 libstdc++6 python3 ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY --from=claude-install /opt/qa/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude
COPY --from=native-package /opt/zg /opt/zg
COPY runtime/zg-provenance.json /opt/qa/zg-provenance.json
COPY runtime/prepare_model.py /opt/qa/prepare_model.py
COPY runtime/smoke_local.sh /opt/qa/smoke_local.sh
RUN ln -s /opt/zg/bin/zg /usr/local/bin/zg \
    && test "$(zg --version)" = "0.0.1" \
    && test "$(claude --version)" = "2.1.212 (Claude Code)"
ENV DISABLE_AUTOUPDATER=1 \
    DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    NO_COLOR=1 \
    ZVEC_GREP_DEVICE=cpu \
    ZVEC_GREP_MODEL_CACHE=/opt/qa/model-cache
RUN python3 /opt/qa/prepare_model.py \
    && sh /opt/qa/smoke_local.sh \
    && python3 /opt/qa/prepare_model.py --record-cache
LABEL org.opencontainers.image.revision="a424e0ce06868dc9ad8f142f57de59b8f9b58699"
WORKDIR /workspace
CMD ["bash"]
