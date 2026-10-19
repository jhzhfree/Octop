from __future__ import annotations

from octop.infra.agents.middleware.knowledge_source_scope import (
    enforce_knowledge_source_args,
    enforce_memory_source_args,
    hide_memory_write_tools,
)

SOURCES = [
    {
        "provider": "akb",
        "mcp_server": "akb__7",
        "knowledge_base_ref": "hr-handbook",
        "selector_argument": "knowledgeBaseCode",
        "read_tools": ["knowledge_query", "knowledge_search"],
        "forbidden_arguments": ["knowledgeBaseId"],
        "access": "read",
    }
]


def test_memory_write_tools_are_removed_from_model_visible_tool_list() -> None:
    tools = [
        {"name": "akb__7_memory_recall"},
        {"name": "akb__7_memory_capture"},
        {"name": "akb__7_memory_forget"},
        {"name": "akb__7_knowledge_search"},
        {"name": "other_memory_memory_capture"},
    ]

    visible = hide_memory_write_tools(tools, ["akb__7"])

    assert visible == [tools[0], tools[3], tools[4]]


def test_scope_injects_approved_selector_for_akb() -> None:
    args, error = enforce_knowledge_source_args(
        "akb__7_knowledge_query", {"question": "休假政策"}, SOURCES
    )

    assert error is None
    assert args == {"question": "休假政策", "knowledgeBaseCode": "hr-handbook"}


def test_scope_injects_ragflow_dataset_selector() -> None:
    args, error = enforce_knowledge_source_args(
        "ragflow__7_retrieve",
        {"question": "service SLA"},
        [
            {
                "provider": "ragflow",
                "mcp_server": "ragflow__7",
                "knowledge_base_ref": "dataset-approved",
                "selector_argument": "dataset_id",
                "read_tools": ["retrieve"],
                "forbidden_arguments": [],
            }
        ],
    )
    assert error is None
    assert args == {"question": "service SLA", "dataset_id": "dataset-approved"}


def test_scope_rejects_a_knowledge_base_outside_the_binding() -> None:
    args, error = enforce_knowledge_source_args(
        "akb__7_knowledge_search",
        {"query": "薪酬", "knowledgeBaseCode": "payroll-private"},
        SOURCES,
    )

    assert args is None
    assert error == "Knowledge request is outside the approved knowledge-base scope"


def test_scope_rejects_alternate_selector_argument() -> None:
    args, error = enforce_knowledge_source_args(
        "akb__7_knowledge_query",
        {"question": "政策", "knowledgeBaseId": 13},
        SOURCES,
    )

    assert args is None
    assert error == "Knowledge request contains a disallowed selector argument"


def test_scope_blocks_tools_not_declared_read_only() -> None:
    args, error = enforce_knowledge_source_args(
        "akb__7_ingest_text", {"content": "payload"}, SOURCES
    )

    assert args is None
    assert error == "This knowledge connection only permits its approved read tools"


def test_scope_handles_products_bound_by_credentials_without_selector() -> None:
    source = {
        "provider": "ragflow",
        "mcp_server": "ragflow__1",
        "knowledge_base_ref": "dataset-8",
        "selector_argument": None,
        "read_tools": ["retrieve", "ask"],
        "forbidden_arguments": [],
        "access": "read",
    }

    args, error = enforce_knowledge_source_args("ragflow__1_retrieve", {"query": "x"}, [source])

    assert error is None
    assert args == {"query": "x"}


def test_scope_requires_selector_when_multiple_bases_share_one_server() -> None:
    sources = SOURCES + [
        {**SOURCES[0], "knowledge_base_ref": "benefits"},
    ]

    args, error = enforce_knowledge_source_args(
        "akb__7_knowledge_query", {"question": "政策"}, sources
    )

    assert args is None
    assert error == "Knowledge request must specify an approved knowledge-base reference"


def test_scope_leaves_unbound_mcp_tools_unchanged() -> None:
    args, error = enforce_knowledge_source_args(
        "docs__2_knowledge_query", {"question": "政策"}, SOURCES
    )

    assert args is None
    assert error is None


MEMORY_SOURCES = [
    {
        "provider": "akb",
        "mcp_server": "akb__7",
        "scope_type": "TASK",
        "scope_code": "weekly-report",
        "read_tools": ["memory_recall"],
        "access": "read",
    }
]


def test_memory_recall_scope_is_fixed_by_worker_binding() -> None:
    args, error = enforce_memory_source_args(
        "akb__7_memory_recall",
        {"query": "上周服务风险", "scopeType": "AGENT", "scopeCode": "other"},
        MEMORY_SOURCES,
        ["akb__7"],
    )

    assert error is None
    assert args == {"query": "上周服务风险", "scopeType": "TASK", "scopeCode": "weekly-report"}


def test_memory_capture_and_forget_are_blocked_even_for_bound_server() -> None:
    for tool in ("memory_capture", "memory_forget"):
        args, error = enforce_memory_source_args(
            f"akb__7_{tool}", {"content": "anything"}, MEMORY_SOURCES, ["akb__7"]
        )

        assert args is None
        assert error == "This memory connection only permits approved read-only recall"


def test_unbound_server_cannot_read_memory_in_virtual_worker_run() -> None:
    args, error = enforce_memory_source_args("akb__7_memory_recall", {"query": "x"}, [], ["akb__7"])

    assert args is None
    assert error == "This memory connection only permits approved read-only recall"
