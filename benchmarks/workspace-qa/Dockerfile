FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep python3 python3-reportlab libgomp1 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN git config --system --add safe.directory /app
WORKDIR /opt/qa
COPY swe-qa-bench/runtime/package.json swe-qa-bench/runtime/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
RUN test "$(./node_modules/.bin/qodercli --version)" = "1.1.45"
COPY swe-qa-bench/scripts/qa-session.py ./qa-session.py
COPY workspace-qa/native_session.py ./native-session.py
COPY workspace-qa/native_index.py ./native-index.py
ENV PATH="/opt/qa/node_modules/.bin:${PATH}"
ENV QODER_EXPOSE_TOKEN_USAGE=1
ARG QA_UID=1001
ARG QA_GID=1001
RUN if ! getent group "${QA_GID}" >/dev/null; then groupadd --gid "${QA_GID}" qa; fi \
    && if ! getent passwd "${QA_UID}" >/dev/null; then useradd --uid "${QA_UID}" --gid "${QA_GID}" --create-home qa; fi \
    && mkdir -p /home/qa && chown "${QA_UID}:${QA_GID}" /home/qa
USER ${QA_UID}:${QA_GID}
ENV HOME=/home/qa
WORKDIR /app
