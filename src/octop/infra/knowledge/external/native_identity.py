"""Native AKB host operations. Keys stay encrypted in the existing secret store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import subprocess
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.external.http import get_json, validate_endpoint
from octop.infra.knowledge.external.registry import get_adapter
from octop.infra.knowledge.external.request_auth import public_jwk, signed_requests
from octop.infra.knowledge.external.types import ExternalSourceError, ExternalSourceSpec


def _failure() -> OctopError:
    return OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)


def _index(owner: dict[str, str]) -> str:
    values = [owner[k] for k in ("platformId", "installationId", "workspaceId", "externalAgentId")]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


class KeyStore:
    def __init__(self, repo: SecretRepo, root: str):
        self.repo = repo
        self.root = Path(root)

    def read(self, name: str) -> dict[str, Any]:
        raw = self.repo.get("agent_identity:" + name)
        return decrypt_credentials(self.repo, raw) if raw else {}

    def write(self, name: str, data: dict[str, Any]) -> None:
        blob = encrypt_credentials(self.repo, data)
        self.repo.get_or_create("agent_identity:" + name, lambda: blob)
        self.repo.rotate("agent_identity:" + name, blob)

    def import_key(self, owner: dict[str, str], pem: str) -> dict[str, Any]:
        jwk = public_jwk(pem)
        reference = _index(owner) + ":" + jwk["kid"]
        self.write(reference, {"pem": pem, "publicJwk": jwk})
        return {"kid": jwk["kid"], "keyReference": reference, "publicJwk": jwk}

    def activate(self, owner: dict[str, str], imported: dict[str, Any]) -> None:
        self.write(_index(owner), imported)

    def state(self, owner: dict[str, str], reference: str = "") -> dict[str, Any]:
        if reference:
            if not reference.startswith(_index(owner) + ":"):
                raise _failure()
            saved = self.read(reference)
            current = (
                {
                    "kid": saved.get("publicJwk", {}).get("kid"),
                    "publicJwk": saved.get("publicJwk"),
                    "keyReference": reference,
                }
                if saved
                else {}
            )
        else:
            current = self.read(_index(owner))
        if not current:
            directory = self.root / _index(owner)
            if (directory / "identity.json").is_file():
                metadata = json.loads(self._legacy_file(directory / "identity.json"))
                if metadata.get("format") != 1 or _index(metadata.get("owner", {})) != _index(
                    owner
                ):
                    raise _failure()
                kid = metadata.get("activeKid")
                if (
                    isinstance(kid, str)
                    and len(kid) == 43
                    and all(c.isalnum() or c in "-_" for c in kid)
                    and any(
                        k.get("kid") == kid and k.get("state") == "READY"
                        for k in metadata.get("keys", [])
                    )
                ):
                    imported = self.import_key(
                        owner, self._legacy_file(directory / kid / "private.pem")
                    )
                    if imported["kid"] != kid:
                        raise _failure()
                    self.activate(owner, imported)
                    current = imported
        return {
            "activeKid": current.get("kid"),
            "keyReference": current.get("keyReference"),
            "keys": [
                {"kid": current["kid"], "publicJwk": current["publicJwk"], "localState": "READY"}
            ]
            if current
            else [],
        }

    def pem(self, owner: dict[str, str], reference: str = "") -> str:
        self.state(owner, reference)
        current = self.read(_index(owner))
        if reference and not reference.startswith(_index(owner) + ":"):
            raise _failure()
        saved = self.read(reference or str(current.get("keyReference") or ""))
        if not saved.get("pem"):
            raise _failure()
        return str(saved["pem"])

    def _legacy_file(self, path: Path) -> str:
        resolved_root = self.root.resolve()
        if not path.resolve().is_relative_to(resolved_root):
            raise _failure()
        for item in (path, *path.parents):
            if item.is_symlink():
                raise _failure()
            if item == self.root:
                break
        if os.name == "nt":
            # Only legacy migration needs the Windows ACL inspector. New keys
            # are encrypted in the shared credential store on every platform.
            script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$data=[Console]::In.ReadToEnd() | ConvertFrom-Json
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$allowed=@($sid,'S-1-5-18','S-1-5-32-544')
$p=[IO.Path]::GetFullPath($data.path)
$root=[IO.Path]::GetFullPath($data.root)
$item=$p
while ($true) {
  $acl=Get-Acl -LiteralPath $item
  if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid) {throw 'owner'}
  foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed) {throw 'acl'}
  }
  if ($item -eq $root) {break}
  $item=[IO.Path]::GetDirectoryName($item)
  if (!$item) {throw 'root'}
}
$info=Get-Item -LiteralPath $p
if ($info.Length -gt 65536) {throw 'size'}
[Console]::Write([IO.File]::ReadAllText($p))
"""
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                input=json.dumps({"path": str(path), "root": str(self.root)}),
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode:
                raise _failure()
            return result.stdout
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > 65536
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise _failure()
            return handle.read(65537)


def retire_reference(store: KeyStore, reference: str, bindings: Any) -> None:
    if not reference or ":" not in reference:
        return
    owner = reference.split(":", 1)[0]
    raw = store.repo.get("agent_identity:" + owner)
    current = decrypt_credentials(store.repo, raw) if raw else {}
    if not bindings.retire_key(reference):
        return
    if raw and current.get("keyReference") == reference:
        store.repo.delete_if_value("agent_identity:" + owner, raw)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").encode("idna").decode("ascii").lower()
    host = "[" + host + "]" if ":" in host else host
    port = parts.port
    suffix = (
        ""
        if port is None or (parts.scheme, port) in {("https", 443), ("http", 80)}
        else ":" + str(port)
    )
    return f"{parts.scheme}://{host}{suffix}"


def _base(payload: dict[str, Any]) -> str:
    return validate_endpoint(
        payload["origin"],
        allow_insecure_http=bool(
            payload.get("allowPrivateHttp") or payload.get("allowInsecureLoopback")
        ),
    )


async def call(payload: dict[str, Any], repo: SecretRepo | None) -> dict[str, Any]:
    op = payload["op"]
    if op == "capabilities":
        return {"version": "1.0.0", "supported": True, "runtime": "python"}
    if repo is None:
        raise _failure()
    store = KeyStore(repo, payload["root"])
    if op == "generate":
        external = str(uuid.UUID(payload.get("externalAgentId") or str(uuid.uuid4())))

        def generate() -> str:
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            return key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode()

        pem = await asyncio.to_thread(generate)
        prefix = "agent_identity:generated:" + payload["keyScope"] + ":"
        pending = store.repo.names(prefix)
        if len(pending) >= 16:
            raise _failure()
        reference = str(uuid.uuid4())
        store.write(
            "generated:" + payload["keyScope"] + ":" + reference, {"pem": pem, "agentId": external}
        )
        jwk = public_jwk(pem)
        return {
            "externalAgentId": external,
            "kid": jwk["kid"],
            "publicJwk": jwk,
            "keyReference": reference,
        }
    if op == "state":
        return await asyncio.to_thread(
            store.state, payload["owner"], str(payload.get("keyReference") or "")
        )
    if op == "connect":
        base = _base(payload)
        result = await get_json(
            base
            + "/api/v1/external/identity/configuration?"
            + urlencode({"agentId": payload["externalAgentId"]}),
            headers={"X-API-Key": payload["apiKey"]},
            timeout_seconds=payload.get("timeoutMs", 10000) / 1000,
            origin="akb",
            status_map={401: "INVALID_CREDENTIALS", 403: "FORBIDDEN", 429: "RATE_LIMITED"},
        )
        binding = (
            result.get("data")
            if isinstance(result, dict) and result.get("success") is True
            else None
        )
        if (
            not isinstance(binding, dict)
            or binding.get("externalAgentId") != payload["externalAgentId"]
            or binding.get("resource") != _origin(base)
        ):
            raise _failure()
        required = (
            "platformId",
            "installationId",
            "workspaceId",
            "externalAgentId",
            "runtimeAgentKey",
            "policyId",
            "applicationPrincipalId",
            "bindingId",
            "issuer",
            "audience",
            "resource",
        )
        if any(
            not isinstance(binding.get(k), str) or not binding[k] or len(binding[k]) > 2048
            for k in required
        ):
            raise _failure()
        owner = {
            k: binding[k]
            for k in ("platformId", "installationId", "workspaceId", "externalAgentId")
        }
        supplied_pem = payload.get("privateKey")
        pem = str(supplied_pem) if supplied_pem else ""
        if payload.get("keyReference"):
            generated = store.read(
                "generated:" + payload["keyScope"] + ":" + payload["keyReference"]
            )
            if pem or generated.get("agentId") != binding["externalAgentId"]:
                raise _failure()
            pem = generated["pem"]
        if not pem:
            if not payload.get("reuseExisting"):
                raise _failure()
            pem = store.pem(owner, str(payload.get("existingKeyReference") or ""))
        imported = store.import_key(owner, pem)
        try:
            with signed_requests(binding, pem, payload["apiKey"], payload["invocation"]):
                response = await get_json(
                    base + "/api/v1/external/identity/status",
                    headers={"X-API-Key": payload["apiKey"]},
                    timeout_seconds=payload.get("timeoutMs", 10000) / 1000,
                    origin="akb",
                    status_map={401: "INVALID_CREDENTIALS", 403: "FORBIDDEN", 429: "RATE_LIMITED"},
                )
        except ExternalSourceError as exc:
            if exc.code in {"INVALID_CREDENTIALS", "FORBIDDEN"}:
                raise _failure() from exc
            raise
        status = response.get("data", {}) if response.get("success") is True else {}
        if (
            status.get("agentId") != binding["externalAgentId"]
            or status.get("kid") != imported["kid"]
            or status.get("keyStatus") != "ACTIVE"
            or str(status.get("policyId")) != binding["policyId"]
            or status.get("proofProfile") != "AGENT_KEY_RS256_V2"
        ):
            raise _failure()
        store.activate(owner, imported)
        return {
            "binding": binding,
            "status": status,
            "keyReference": imported["keyReference"],
            **store.state(owner),
        }
    if op == "execute":
        binding = payload["binding"]
        spec = ExternalSourceSpec(**payload["spec"])
        if _origin(spec.endpoint) != binding["resource"]:
            raise _failure()
        adapter = get_adapter(spec.kind)
        if adapter is None:
            raise _failure()
        pem = await asyncio.to_thread(
            store.pem, payload["owner"], str(binding.get("keyReference") or "")
        )
        creds = {"api_key": payload["apiKey"], "auth_type": "agent"}
        with signed_requests(binding, pem, payload["apiKey"], payload["invocation"]):
            if payload["request"]["operation"] == "bases":
                return {
                    "items": [
                        {"baseCode": item["ref"], "name": item["name"]}
                        for item in await adapter.list_refs(spec, creds)
                    ]
                }
            result = await adapter.search(
                spec, creds, payload["request"]["query"], top_k=payload["request"]["topK"]
            )
        return {"chunks": [asdict(chunk) for chunk in result.chunks], "trace_ids": result.trace_ids}
    raise _failure()
