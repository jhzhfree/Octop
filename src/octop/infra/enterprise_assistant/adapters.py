"""Bounded deterministic connectors. Credentials and URLs never come from model output."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib
import io
import os
import ssl
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from .models import ConnectionConfig, Features

MAX_BYTES = 1024 * 1024


class WorkflowError(Exception):
    """Stable, non-secret error codes suitable for stored evidence."""


def origin(url: str) -> str:
    try:
        p = urlsplit(url)
        if (
            p.scheme not in {"https", "http", "ssh"}
            or not p.hostname
            or p.username
            or p.password
            or p.fragment
        ):
            raise ValueError
        host = p.hostname.lower().rstrip(".")
        if host in {"169.254.169.254", "metadata.google.internal", "metadata"}:
            raise ValueError
        if ":" in host:
            host = f"[{host}]"
        port = p.port
        suffix = (
            f":{port}" if port and port != ({"https": 443, "http": 80, "ssh": 22}[p.scheme]) else ""
        )
        return f"{p.scheme}://{host}{suffix}"
    except ValueError as exc:
        raise WorkflowError("DESTINATION_INVALID") from exc


def check_url(url: str, flags: Features) -> str:
    value = origin(url)
    if value not in flags.allowed_origins or (value.startswith("http:") and not flags.allow_http):
        raise WorkflowError("DESTINATION_BLOCKED")
    return url


def validate_connection(config: ConnectionConfig, flags: Features) -> None:
    check_url(config.url, flags)
    parsed = urlsplit(config.url)
    ssh = config.kind == "inspection" and config.inspection_transport == "ssh"
    if (parsed.scheme == "ssh") != ssh:
        raise WorkflowError("URL_SCHEME_MISMATCH")
    if ssh and parsed.path not in {"", "/"}:
        raise WorkflowError("SSH_HOST_URL_REQUIRED")
    if parsed.query:
        # A webhook key is provided separately in the encrypted secret field.
        raise WorkflowError("URL_QUERY_NOT_ALLOWED")
    if config.kind == "wecom" and (
        origin(config.url) != "https://qyapi.weixin.qq.com"
        or parsed.path.rstrip("/") != "/cgi-bin/webhook/send"
    ):
        raise WorkflowError("WECOM_WEBHOOK_REQUIRED")


async def json_request(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> Any:
    try:
        async with client.stream(method, url, **kwargs) as response:
            if response.status_code in {401, 403}:
                raise WorkflowError("AUTHENTICATION_FAILED")
            if response.status_code < 200 or response.status_code >= 300:
                raise WorkflowError(f"UPSTREAM_HTTP_{response.status_code}")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise WorkflowError("RESPONSE_TOO_LARGE")
            if not raw:
                return None
            import json

            try:
                return json.loads(raw)
            except ValueError as exc:
                raise WorkflowError("UPSTREAM_INVALID_JSON") from exc
    except httpx.HTTPError as exc:
        # Do not persist exception strings: they can contain webhook keys or auth headers.
        raise WorkflowError("UPSTREAM_UNAVAILABLE") from exc


def _endpoint(config: ConnectionConfig, path: str) -> str:
    return config.url.rstrip("/") + path


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    bundle = os.environ.get("OCTOP_ENTERPRISE_CA_BUNDLE")
    if bundle:
        context.load_verify_locations(cafile=bundle)
    return context


def _client(config: ConnectionConfig, secret: str) -> httpx.AsyncClient:
    headers = {"Accept": "application/json"}
    auth = None
    if config.auth == "bearer":
        headers["Authorization"] = "Bearer " + secret
    elif config.auth == "basic":
        auth = httpx.BasicAuth(config.username, secret)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=10),
        follow_redirects=False,
        trust_env=False,
        verify=tls_context(),
        headers=headers,
        auth=auth,
    )


async def collect(config: ConnectionConfig, secret: str, flags: Features) -> list[dict[str, Any]]:
    validate_connection(config, flags)
    if config.kind == "oa":
        return await collect_oa(config, secret, flags)
    if config.kind == "inspection" and config.inspection_transport == "ssh":
        return await asyncio.get_running_loop().run_in_executor(None, collect_ssh, config, secret)
    async with _client(config, secret) as client:
        if config.kind == "inspection":
            value = await json_request(client, "GET", config.url)
            if not isinstance(value, dict):
                raise WorkflowError("HEALTH_JSON_OBJECT_REQUIRED")
            # Store only health facts, never an arbitrary monitoring response containing secrets.
            status = str(value.get("status", "unknown"))[:80]
            if status.lower() not in {"ok", "healthy", "up", "pass"}:
                raise WorkflowError("HEALTH_CHECK_FAILED")
            return [{"type": "health", "name": config.name, "status": status}]
        if config.kind != "jira":
            raise WorkflowError("READ_CONNECTOR_REQUIRED")
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        for page in range(10):
            body: dict[str, Any] = {
                "jql": config.jql,
                "maxResults": 50,
                "fields": ["summary", "status", "duedate", "updated"],
            }
            path = f"/rest/api/{config.jira_api}/search"
            if config.jira_api == "3":
                path += "/jql"
                if page_token:
                    body["nextPageToken"] = page_token
            else:
                body["startAt"] = page * 50
            value = await json_request(client, "POST", _endpoint(config, path), json=body)
            if not isinstance(value, dict) or not isinstance(value.get("issues"), list):
                raise WorkflowError("JIRA_SCHEMA_INVALID")
            issues = value["issues"]
            for issue in issues:
                fields = issue.get("fields") or {}
                key = str(issue.get("key", ""))[:120]
                records.append(
                    {
                        "type": "jira",
                        "key": key,
                        "summary": str(fields.get("summary", ""))[:1000],
                        "status": str((fields.get("status") or {}).get("name", ""))[:120],
                        "due": str(fields.get("duedate") or "")[:40],
                        "updated": str(fields.get("updated") or "")[:40],
                        "url": _endpoint(config, "/browse/" + quote(key, safe="")),
                    }
                )
            if config.jira_api == "3":
                page_token = value.get("nextPageToken")
                if value.get("isLast") is True or not page_token:
                    return records
            elif len(issues) < 50 or len(records) >= int(value.get("total", 501)):
                return records
        # Refuse silent truncation: a report must not claim a partial collection is complete.
        raise WorkflowError("JIRA_RESULT_LIMIT")


async def collect_oa(
    config: ConnectionConfig, secret: str, flags: Features
) -> list[dict[str, Any]]:
    from playwright.async_api import Error as BrowserError
    from playwright.async_api import async_playwright

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                executable_path=os.environ.get("OCTOP_ENTERPRISE_CHROMIUM") or None,
            )
            context = await browser.new_context(accept_downloads=False, service_workers="block")

            async def route(request_route: Any) -> None:
                try:
                    check_url(request_route.request.url, flags)
                    if request_route.request.method not in {"GET", "HEAD"} and origin(
                        request_route.request.url
                    ) != origin(config.url):
                        raise WorkflowError("OA_CROSS_ORIGIN_WRITE_BLOCKED")
                except WorkflowError:
                    await request_route.abort()
                else:
                    await request_route.continue_()

            await context.route("**/*", route)

            # WebSockets are unnecessary for deterministic OA extraction.
            async def block_websocket(ws: Any) -> None:
                await ws.close()

            await context.route_web_socket("**/*", block_websocket)
            page = await context.new_page()
            page.set_default_timeout(15000)
            await page.goto(config.url, wait_until="domcontentloaded", timeout=30000)
            if origin(page.url) != origin(config.url):
                raise WorkflowError("OA_LOGIN_REDIRECT_BLOCKED")
            await page.locator(config.username_selector).fill(config.username)
            await page.locator(config.password_selector).fill(secret)
            await page.locator(config.login_selector).click()
            await page.locator(config.ready_selector).wait_for(state="visible")
            check_url(page.url, flags)
            items = page.locator(config.items_selector)
            count = await items.count()
            if count > 500:
                raise WorkflowError("OA_RESULT_LIMIT")
            records = [
                {"type": "oa", "summary": (await items.nth(i).inner_text())[:2000]}
                for i in range(count)
            ]
            await browser.close()
            return records
    except BrowserError as exc:
        # No captcha/SSO bypass. Operator must repair selectors or interactive login policy.
        raise WorkflowError("OA_LOGIN_OR_BROWSER_REQUIRED") from exc


def report_chunks(content: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in content:
        if len((current + char).encode("utf-8")) > 2000:
            chunks.append(current)
            current = ""
        current += char
    if current:
        chunks.append(current)
    return chunks


async def send_wecom(
    config: ConnectionConfig, secret: str, content: str, flags: Features, receipt: Any
) -> None:
    validate_connection(config, flags)
    p = urlsplit(config.url)
    url = urlunsplit((p.scheme, p.netloc, p.path, "key=" + quote(secret, safe=""), ""))
    async with _client(config, "") as client:
        for index, chunk in enumerate(report_chunks(content)):
            receipt({"type": "wecom_attempt", "chunk": index + 1})
            value = await json_request(
                client, "POST", url, json={"msgtype": "text", "text": {"content": chunk}}
            )
            if not isinstance(value, dict) or value.get("errcode") != 0:
                raise WorkflowError("WECOM_DELIVERY_REJECTED")
            # Callback persists each acknowledgement before attempting the next chunk.
            receipt({"type": "wecom_ack", "chunk": index + 1, "accepted": True})


async def deploy_vm(
    config: ConnectionConfig,
    secret: str,
    name: str,
    flags: Features,
    before_deploy: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """vSphere 7/8 VM-template content-library deployment (OS comes from approved template)."""
    validate_connection(config, flags)
    async with httpx.AsyncClient(
        timeout=180, follow_redirects=False, trust_env=False, verify=tls_context()
    ) as client:
        token = await json_request(
            client,
            "POST",
            _endpoint(config, "/api/session"),
            auth=httpx.BasicAuth(config.username, secret),
        )
        if not isinstance(token, str) or not token:
            raise WorkflowError("VCENTER_SESSION_INVALID")
        client.headers["vmware-api-session-id"] = token
        try:
            existing = await json_request(
                client, "GET", _endpoint(config, "/api/vcenter/vm"), params={"names": name}
            )
            if not isinstance(existing, list):
                raise WorkflowError("VCENTER_INVENTORY_INVALID")
            if existing:
                raise WorkflowError("VM_NAME_ALREADY_EXISTS")
            body: dict[str, Any] = {
                "name": name,
                "powered_on": True,
                "placement": {
                    "folder": config.folder,
                    "resource_pool": config.resource_pool,
                },
                "vm_home_storage": {"datastore": config.datastore},
                "disk_storage": {"datastore": config.datastore},
            }
            if config.customization_spec:
                body["guest_customization"] = {"name": config.customization_spec}
            if before_deploy is not None:
                before_deploy()
            vm_id = await json_request(
                client,
                "POST",
                _endpoint(
                    config,
                    "/api/vcenter/vm-template/library-items/" + quote(config.template_id, safe=""),
                ),
                params={"action": "deploy"},
                json=body,
            )
            if not isinstance(vm_id, str) or not vm_id:
                raise WorkflowError("VCENTER_DEPLOY_RECEIPT_INVALID")
            return {
                "type": "vmware_receipt",
                "vm_id": vm_id,
                "name": name,
                "os": config.os_label,
                "state": "deployed",
                "note": "Guest boot and OS customization require vCenter verification",
            }
        finally:
            # Cleanup failure must not hide an acknowledged VM creation.
            with contextlib.suppress(httpx.HTTPError):
                await client.delete(_endpoint(config, "/api/session"), timeout=5)


SSH_COMMANDS = ("uname -sr", "uptime", "df -P", "free -m")


def collect_ssh(config: ConnectionConfig, secret: str) -> list[dict[str, Any]]:
    """Linux read-only inspection with a pinned host key and an explicit service-account key."""
    try:
        ssh = importlib.import_module("paramiko")
    except ImportError as exc:
        raise WorkflowError("SSH_EXTRA_NOT_INSTALLED") from exc
    client = ssh.SSHClient()
    try:
        parts = config.ssh_host_key.strip().split()
        if len(parts) < 2:
            raise WorkflowError("SSH_HOST_KEY_INVALID")
        server_key = ssh.PKey.from_type_string(parts[0], base64.b64decode(parts[1], validate=True))
        parsed = urlsplit(config.url)
        host = str(parsed.hostname)
        port = parsed.port or 22
        known_host = host if port == 22 else f"[{host}]:{port}"
        client.get_host_keys().add(known_host, parts[0], server_key)
        client.set_missing_host_key_policy(ssh.RejectPolicy())
        private_key = None
        for key_class in (ssh.Ed25519Key, ssh.ECDSAKey, ssh.RSAKey):
            try:
                private_key = key_class.from_private_key(io.StringIO(secret))
                break
            except (ssh.SSHException, ValueError):
                continue
        if private_key is None:
            raise WorkflowError("SSH_PRIVATE_KEY_INVALID")
        client.connect(
            hostname=host,
            port=port,
            username=config.username,
            pkey=private_key,
            allow_agent=False,
            look_for_keys=False,
            timeout=10,
            auth_timeout=10,
            banner_timeout=10,
        )
        facts: list[dict[str, Any]] = []
        for command in SSH_COMMANDS:
            stdin, stdout, stderr = client.exec_command(command, timeout=15)
            stdin.close()
            output = stdout.read(16001)
            error = stderr.read(16001)
            if len(output) > 16000 or len(error) > 16000:
                raise WorkflowError("SSH_OUTPUT_LIMIT")
            if stdout.channel.recv_exit_status() != 0:
                raise WorkflowError("SSH_INSPECTION_COMMAND_FAILED")
            facts.append(
                {
                    "type": "ssh_fact",
                    "name": config.name,
                    "command": command,
                    "output": output.decode("utf-8", errors="replace"),
                    "status": "collected",
                }
            )
        return facts
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("SSH_AUTH_HOST_KEY_OR_CONNECTION_FAILED") from exc
    finally:
        client.close()
