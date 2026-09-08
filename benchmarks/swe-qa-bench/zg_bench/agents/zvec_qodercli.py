from __future__ import annotations

from .qodercli import QODER_CONFIG_DIR, QoderCLI
from .zvec_grep import ZvecGrepMixin


class ZvecQoderCLI(ZvecGrepMixin, QoderCLI):
    """Qoder CLI benchmark agent with the zvec-grep MCP server provisioned."""

    def _mcp_install_environment(self) -> dict[str, str]:
        return {"QODER_CONFIG_DIR": QODER_CONFIG_DIR}
