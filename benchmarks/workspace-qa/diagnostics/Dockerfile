FROM zg-readonly-qa:0.2.2
USER root
COPY workspace-qa/diagnostics/prompt-audit.mjs /opt/qa/prompt-audit.mjs
COPY workspace-qa/diagnostics/patch_qoder.py /opt/qa/patch_qoder.py
RUN python3 /opt/qa/patch_qoder.py /opt/qa/node_modules/@qoder-ai/qodercli/bundle/qodercli.js /opt/qa/prompt-audit.mjs /opt/qa/diagnostic-patch.json \
    && node --check /opt/qa/node_modules/@qoder-ai/qodercli/bundle/qodercli.js
ARG QA_UID=1001
ARG QA_GID=1001
USER ${QA_UID}:${QA_GID}
WORKDIR /app
