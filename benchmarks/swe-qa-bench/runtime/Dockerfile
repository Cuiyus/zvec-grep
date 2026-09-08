FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep python3 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git config --system --add safe.directory /app
WORKDIR /opt/qa
COPY runtime/package.json runtime/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY scripts/readonly-search.mjs ./readonly-search.mjs
COPY scripts/prepare-index.mjs ./prepare-index.mjs
ENV PATH="/opt/qa/node_modules/.bin:${PATH}"
ENV OPENCODE_DISABLE_AUTOUPDATE=true
ENV OPENCODE_DISABLE_LSP_DOWNLOAD=true
ENV OPENCODE_DISABLE_EXTERNAL_SKILLS=true
ARG QA_UID=1001
ARG QA_GID=1001
RUN if ! getent group "${QA_GID}" >/dev/null; then groupadd --gid "${QA_GID}" qa; fi \
    && if ! getent passwd "${QA_UID}" >/dev/null; then useradd --uid "${QA_UID}" --gid "${QA_GID}" --create-home qa; fi
USER ${QA_UID}:${QA_GID}
WORKDIR /app
