from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.base import message_to_dict

from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.scenes.evidence import latest_assistant_answer, scene_run_evidence


def test_latest_assistant_answer_returns_visible_text_without_reasoning_or_tools() -> None:
    wires = [
        message_to_dict(AIMessage(content="Earlier result")),
        message_to_dict(
            AIMessage(
                content=[
                    {"type": "thinking", "thinking": "internal reasoning"},
                    {"type": "text", "text": "Final report"},
                ]
            )
        ),
        message_to_dict(ToolMessage(content="connector payload", tool_call_id="call-1")),
    ]

    assert latest_assistant_answer(wires) == "Final report"


def test_latest_assistant_answer_strips_thinking_tags_from_plain_text() -> None:
    wires = [message_to_dict(AIMessage(content="<think>private</think>Visible report"))]

    assert latest_assistant_answer(wires) == "Visible report"


async def test_scene_run_evidence_reads_matching_requester_projection() -> None:
    run = {"cron_id": "cron-1", "agent_id": "agent-1", "requested_by": 7}
    session_key = ThreadRegistry.make_key(
        agent_id="agent-1",
        channel_type="cron",
        channel_subject_id="cron-1",
        channel_chat_type=ThreadRegistry.CHAT_TYPE_DM,
    )
    cron_manager = Mock()
    cron_manager.get.return_value = SimpleNamespace(session_key=session_key)
    thread_registry = Mock()
    thread_registry.get_session.return_value = SimpleNamespace(
        agent_id="agent-1", user_id=7, thread_id="thread-1"
    )
    thread_message_repo = Mock()
    thread_message_repo.projection_status.return_value = "ready"
    messages = [
        message_to_dict(ToolMessage(content="private connector payload", tool_call_id="call-1")),
        message_to_dict(AIMessage(content="Visible result")),
    ]
    thread_message_repo.page.return_value = (
        [SimpleNamespace(message_json=json.dumps(message)) for message in messages],
        False,
    )

    answer = await scene_run_evidence(
        run=run,
        cron_manager=cron_manager,
        thread_registry=thread_registry,
        agent_registry=Mock(),
        thread_message_repo=thread_message_repo,
        history_archive=None,
    )

    assert answer == "Visible result"
    thread_message_repo.page.assert_called_once_with("thread-1", limit=200)


async def test_scene_run_evidence_rejects_session_owned_by_another_user() -> None:
    run = {"cron_id": "cron-1", "agent_id": "agent-1", "requested_by": 7}
    session_key = ThreadRegistry.make_key(
        agent_id="agent-1",
        channel_type="cron",
        channel_subject_id="cron-1",
        channel_chat_type=ThreadRegistry.CHAT_TYPE_DM,
    )
    cron_manager = Mock()
    cron_manager.get.return_value = SimpleNamespace(session_key=session_key)
    thread_registry = Mock()
    thread_registry.get_session.return_value = SimpleNamespace(
        agent_id="agent-1", user_id=8, thread_id="thread-1"
    )
    thread_message_repo = Mock()

    answer = await scene_run_evidence(
        run=run,
        cron_manager=cron_manager,
        thread_registry=thread_registry,
        agent_registry=Mock(),
        thread_message_repo=thread_message_repo,
        history_archive=None,
    )

    assert answer is None
    thread_message_repo.projection_status.assert_not_called()
