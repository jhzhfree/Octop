"""Optional integration: AKB_REPO=/path/to/compiled/akb python tests/java_interop.py."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from akb_identity_sdk import create_request_proof, generate_key_pair, generate_uuid


def main() -> None:
    repo = Path(os.environ["AKB_REPO"])
    reports = list((repo / "backend/target/surefire-reports").glob("TEST-*.xml"))
    if not reports:
        raise RuntimeError("Run AKB Maven tests before this integration check")
    properties = ET.parse(reports[0]).getroot().findall("./properties/property")
    classpath = next(
        prop.attrib["value"] for prop in properties if prop.get("name") == "java.class.path"
    )
    pair = generate_key_pair()
    agent = generate_uuid()
    binding = {
        "policyId": "17",
        "applicationPrincipalId": "123",
        "bindingId": "21",
        "externalAgentId": agent,
        "runtimeAgentKey": agent,
        "issuer": "https://akb.local/agent-identity",
        "audience": "akb-external-api",
        "resource": "https://akb.example",
        "platformId": "akb-service",
        "installationId": "install",
        "workspaceId": "scope",
    }
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": "knowledge_search",
                "arguments": {"query": "中文查询 🔎", "knowledgeBaseId": "KB_HR"},
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    credential = "synthetic_java_interop_key"
    with tempfile.TemporaryDirectory(prefix="akb-python-java-") as directory:
        root = Path(directory)
        source = Path(__file__).parent / "java/AkbVerifierProbe.java"
        subprocess.run(
            [
                "java",
                "-m",
                "jdk.compiler/com.sun.tools.javac.Main",
                "-cp",
                classpath,
                "-d",
                directory,
                str(source),
            ],
            check=True,
            timeout=60,
        )
        proof = create_request_proof(
            binding=binding,
            private_pem=pair.private_pem,
            service_key=credential,
            method="POST",
            url="https://akb.example/gateway/api/mcp",
            body=body.encode(),
            session_id="sdk-test",
            invocation_id=generate_uuid(),
            terminal_agent="build",
        )
        fixture = root / "fixture.json"
        fixture.write_text(
            json.dumps(
                {
                    "binding": binding,
                    "publicJwk": pair.public_jwk,
                    "proof": proof,
                    "body": body,
                    "serviceKey": credential,
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            ["java", "-cp", directory + os.pathsep + classpath, "AkbVerifierProbe", str(fixture)],
            check=True,
            timeout=60,
        )


if __name__ == "__main__":
    main()
