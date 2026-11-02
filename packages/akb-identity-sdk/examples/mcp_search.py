"""Run after registering this host's UUID/public JWK and binding an AKB Agent credential.

Required env: AKB_URL, AKB_SERVICE_KEY, AKB_AGENT_UUID, AKB_PRIVATE_KEY_FILE, AKB_KB_ID.
The private key file is owned/protected by the host, outside model-readable workspaces.
"""

from __future__ import annotations

import os
from pathlib import Path

from akb_identity_sdk import AkbMcpClient, tool_payload


def main() -> None:
    client = AkbMcpClient(
        os.environ["AKB_URL"],
        os.environ["AKB_SERVICE_KEY"],
        allow_private_http=os.environ.get("AKB_ALLOW_PRIVATE_HTTP") == "1",
    )
    client.connect()
    print("Tools:", client.list_tools())
    client.authenticate_agent(
        os.environ["AKB_AGENT_UUID"],
        Path(os.environ["AKB_PRIVATE_KEY_FILE"]).read_text(encoding="utf-8"),
        agent_name=os.environ.get("AKB_AGENT_NAME", "third-party-agent"),
    )
    print("Knowledge bases:", tool_payload(client.list_knowledge_bases()))
    print(tool_payload(client.search(os.environ["AKB_KB_ID"], "报销申请需要什么材料？")))


if __name__ == "__main__":
    main()
