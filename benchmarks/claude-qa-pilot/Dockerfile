FROM node:24-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git libgomp1 python3 ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/qa
COPY runtime/package.json runtime/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && test "$(./node_modules/.bin/claude --version)" = "2.1.212 (Claude Code)" \
    && test "$(./node_modules/.bin/zg --version)" = "0.2.2"

ENV PATH="/opt/qa/node_modules/.bin:${PATH}" \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    NO_COLOR=1 \
    ZVEC_GREP_DEVICE=cpu \
    ZVEC_GREP_MODE=direct

WORKDIR /workspace
CMD ["bash"]
