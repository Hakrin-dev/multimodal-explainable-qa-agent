"""Trace data model & collector — the explainability backbone (§4.1).

Contract draft v0.1 (to be frozen with B & C at end of W1).
Authoritative JSON Schema: docs/contracts/trace_schema.json

A TraceNode is produced for every meaningful step of a turn: intent
classification, planning, each tool call (nl2sql / rag_search / formula_eval /
db_lookup_entity / ask_user), each LLM call, fusion, clarification.  A turn
yields one tree; the frontend renders it as timeline + DAG.
"""

from __future__ import annotations

import datetime as dt
import itertools
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class NodeType(str, Enum):
    TURN = "turn"            # root of one user question
    INTENT = "intent"        # intent classification result
    PLAN = "plan"            # task DAG planning
    TOOL_CALL = "tool_call"  # any tool invocation (nl2sql, rag_search, ...)
    LLM_CALL = "llm_call"    # individual LLM invocation
    FUSE = "fuse"            # cross-source fusion generation
    CLARIFY = "clarify"      # clarification round
    STEP = "step"            # generic pipeline step (rewrite, validate, ...)


class NodeStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    ERROR = "error"
    DEGRADED = "degraded"    # succeeded with fallback / partial result
    SKIPPED = "skipped"


@dataclass
class TraceNode:
    id: str
    type: NodeType
    label: str
    parent_id: str | None = None
    input: Any = None
    output: Any = None
    latency_ms: int = 0
    status: NodeStatus = NodeStatus.PENDING
    detail: dict[str, Any] = field(default_factory=dict)  # structured payload
    started_at: str = ""
    children: list["TraceNode"] = field(default_factory=list)

    def to_dict(self, recursive: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "parent_id": self.parent_id,
            "type": self.type.value,
            "label": self.label,
            "input": self.input,
            "output": self.output,
            "latency_ms": self.latency_ms,
            "status": self.status.value,
            "detail": self.detail,
            "started_at": self.started_at,
        }
        if recursive:
            d["children"] = [c.to_dict() for c in self.children]
        return d


class TraceCollector:
    """Collects one trace tree per turn. Thread-safe."""

    def __init__(self, turn_id: str | None = None, question: str = ""):
        self.turn_id = turn_id or uuid.uuid4().hex[:12]
        self.question = question
        self.root = TraceNode(
            id=self.turn_id, type=NodeType.TURN, label="turn",
            input={"question": question}, started_at=_now(),
        )
        self._by_id: dict[str, TraceNode] = {self.root.id: self.root}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    # -- node lifecycle -----------------------------------------------------

    def _next_id(self) -> str:
        return f"{self.turn_id}-n{next(self._ids)}"

    def _attach(self, node: TraceNode, parent: TraceNode | str | None) -> None:
        if parent is None:
            p = self.root
        elif isinstance(parent, str):
            p = self._by_id.get(parent, self.root)
        else:
            p = parent
        node.parent_id = p.id
        p.children.append(node)
        self._by_id[node.id] = node

    @contextmanager
    def span(self, label: str, type: NodeType | str = NodeType.STEP,
             parent: TraceNode | str | None = None, input: Any = None) -> Iterator[TraceNode]:
        node = TraceNode(
            id=self._next_id(), type=NodeType(type), label=label,
            input=input, started_at=_now(),
        )
        with self._lock:
            self._attach(node, parent)
        t0 = time.monotonic()
        try:
            yield node
            if node.status == NodeStatus.PENDING:
                node.status = NodeStatus.OK
        except Exception as e:  # noqa: BLE001 — record then re-raise
            node.status = NodeStatus.ERROR
            node.output = str(e)
            raise
        finally:
            node.latency_ms = int((time.monotonic() - t0) * 1000)

    def child_of(self, node: TraceNode, label: str, type: NodeType | str = NodeType.STEP,
                 input: Any = None) -> TraceNode:
        c = TraceNode(id=self._next_id(), type=NodeType(type), label=label,
                      input=input, started_at=_now())
        with self._lock:
            self._attach(c, node)
        return c

    def finish(self, node: TraceNode, output: Any = None,
               status: NodeStatus = NodeStatus.OK,
               detail: dict[str, Any] | None = None, latency_ms: int | None = None) -> None:
        if output is not None:
            node.output = output
        if detail:
            node.detail.update(detail)
        node.status = status
        if latency_ms is not None:
            node.latency_ms = latency_ms

    # -- export -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        self.root.latency_ms = sum(
            c.latency_ms for c in _walk(self.root)
        )
        return {
            "trace_version": "0.1",
            "turn_id": self.turn_id,
            "question": self.question,
            "created_at": self.root.started_at,
            "root": self.root.to_dict(),
        }

    def flat_events(self) -> list[dict[str, Any]]:
        """Flat list (topological order) — the persistence/event-stream shape."""
        return [n.to_dict(recursive=False) for n in _walk(self.root)]


def _walk(node: TraceNode) -> Iterator[TraceNode]:
    for c in node.children:
        yield from _walk(c)
    yield node


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")
