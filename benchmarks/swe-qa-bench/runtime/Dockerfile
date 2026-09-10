FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep python3 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git config --system --add safe.directory /app
WORKDIR /opt/qa
COPY runtime/package.json runtime/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
RUN test "$(./node_modules/.bin/qodercli --version)" = "1.1.45" && test "$(./node_modules/.bin/opencode --version)" = "1.18.4"
COPY scripts/readonly-search.mjs ./readonly-search.mjs
COPY scripts/replay-search.mjs ./replay-search.mjs
COPY scripts/prepare-index.mjs ./prepare-index.mjs
COPY scripts/qa-session.py ./qa-session.py
ENV PATH="/opt/qa/node_modules/.bin:${PATH}"
ENV OPENCODE_DISABLE_AUTOUPDATE=true
ENV OPENCODE_DISABLE_LSP_DOWNLOAD=true
ENV OPENCODE_DISABLE_EXTERNAL_SKILLS=true
ENV QODER_EXPOSE_TOKEN_USAGE=1
ARG QA_UID=1001
ARG QA_GID=1001
RUN if ! getent group "${QA_GID}" >/dev/null; then groupadd --gid "${QA_GID}" qa; fi \
    && if ! getent passwd "${QA_UID}" >/dev/null; then useradd --uid "${QA_UID}" --gid "${QA_GID}" --create-home qa; fi
USER ${QA_UID}:${QA_GID}
WORKDIR /app
