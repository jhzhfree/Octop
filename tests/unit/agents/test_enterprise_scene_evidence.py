from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.base import message_to_dict

from octop.infra.scenes.evidence import latest_assistant_answer


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
