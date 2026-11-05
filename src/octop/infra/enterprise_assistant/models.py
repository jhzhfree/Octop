"""Validated configuration contracts shared by HTTP, workers and Agent tools."""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["inspection", "jira", "oa", "wecom", "vmware"]
Module = Literal["inspection", "work_items", "reports", "vmware"]
MODULE_FOR: dict[str, Module] = {
    "inspection": "inspection",
    "jira": "work_items",
    "oa": "work_items",
    "wecom": "reports",
    "vmware": "vmware",
    "report": "reports",
}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Features(Contract):
    enabled: bool = False
    inspection: bool = False
    work_items: bool = False
    reports: bool = False
    vmware: bool = False
    # Only administrators may authorize outbound destinations (including intranet).
    allowed_origins: list[str] = Field(default_factory=list, max_length=100)
    allow_http: bool = False


class ConnectionConfig(Contract):
    name: str = Field(min_length=1, max_length=120)
    kind: Kind
    url: str = Field(min_length=1, max_length=2000)
    inspection_transport: Literal["http", "ssh"] = "http"
    ssh_host_key: str = Field(default="", max_length=16000)
    username: str = Field(default="", max_length=256)
    # Jira supports on-premises PAT (bearer), or email + token (basic).
    auth: Literal["none", "bearer", "basic"] = "none"
    jira_api: Literal["2", "3"] = "2"
    jql: str = Field(
        default="assignee = currentUser() AND resolution = Unresolved", max_length=2000
    )
    # OA: selectors are deterministic and never supplied by an LLM.
    username_selector: str = Field(default="input[name=username]", max_length=500)
    password_selector: str = Field(default="input[type=password]", max_length=500)
    login_selector: str = Field(default="button[type=submit]", max_length=500)
    ready_selector: str = Field(default="", max_length=500)
    items_selector: str = Field(default="", max_length=500)
    # Content-library VM templates are approved by the connection owner, never the model.
    template_id: str = Field(default="", max_length=128)
    os_label: str = Field(default="", max_length=120)
    folder: str = Field(default="", max_length=128)
    resource_pool: str = Field(default="", max_length=128)
    datastore: str = Field(default="", max_length=128)
    customization_spec: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def validate_kind(self) -> ConnectionConfig:
        if (
            self.kind == "inspection"
            and self.inspection_transport == "ssh"
            and not (self.ssh_host_key and self.username)
        ):
            raise ValueError("SSH requires username and pinned OpenSSH server public key")
        if self.kind == "oa" and not (self.ready_selector and self.items_selector):
            raise ValueError("OA requires ready_selector and items_selector")
        if self.kind == "vmware" and not all(
            (self.template_id, self.os_label, self.folder, self.resource_pool, self.datastore)
        ):
            raise ValueError("VMware requires an approved OS template and placement IDs")
        return self


class ConnectionInput(Contract):
    config: ConnectionConfig
    # An empty secret on update preserves the existing encrypted secret.
    secret: str = Field(default="", max_length=16000, repr=False)
    expected_version: int | None = Field(default=None, gt=0)


class JobConfig(Contract):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["inspection", "jira", "oa", "report", "vmware"]
    agent_id: str = Field(min_length=1, max_length=128)
    connection_id: str = Field(min_length=1, max_length=128)
    # Reports collect only successful read-only runs belonging to these jobs.
    source_job_ids: list[str] = Field(default_factory=list, max_length=30)
    enabled: bool = False
    cron: str = Field(default="", max_length=100)
    timezone: str = Field(default="Asia/Shanghai", max_length=80)
    vm_name: str = Field(default="", max_length=80, pattern=r"^[a-zA-Z0-9_.-]*$")

    @model_validator(mode="after")
    def validate_schedule(self) -> JobConfig:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA time zone") from exc
        if self.cron:
            CronTrigger.from_crontab(self.cron, timezone=self.timezone)
        if self.kind == "vmware" and (self.cron or not self.vm_name):
            raise ValueError("VM creation requires a name and must be requested manually")
        if self.kind == "report" and not self.source_job_ids:
            raise ValueError("Report requires source jobs")
        return self


class JobInput(Contract):
    config: JobConfig
    expected_version: int | None = Field(default=None, gt=0)


class Decision(Contract):
    expected_version: int = Field(gt=0)
    content_hash: str = Field(min_length=64, max_length=64)
    approve: bool


class DraftEdit(Contract):
    expected_version: int = Field(gt=0)
    content: str = Field(min_length=1, max_length=40000)
