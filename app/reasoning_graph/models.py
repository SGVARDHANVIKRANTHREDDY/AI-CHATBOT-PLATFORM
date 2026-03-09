from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NodeType(StrEnum):
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    MEMORY_LOOKUP = "memory_lookup"


class ReasoningNode(BaseModel):
    id: str
    type: NodeType
    task: str
    dependencies: list[str] = Field(default_factory=list)
    result: Any | None = None
    status: NodeStatus = NodeStatus.PENDING
    depth: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningGraph(BaseModel):
    nodes: dict[str, ReasoningNode] = Field(default_factory=dict)

    # Complexity Limits
    MAX_NODES: int = 50
    MAX_DEPTH: int = 10

    def add_node(self, node: ReasoningNode):
        if len(self.nodes) >= self.MAX_NODES:
            raise ValueError(f"Exceeded maximum graph nodes ({self.MAX_NODES})")

        # Calculate depth
        if not node.dependencies:
            node.depth = 1
        else:
            max_dep_depth = max(self.nodes[d].depth for d in node.dependencies if d in self.nodes)
            node.depth = max_dep_depth + 1

        if node.depth > self.MAX_DEPTH:
            raise ValueError(f"Exceeded maximum graph depth ({self.MAX_DEPTH})")

        self.nodes[node.id] = node

    def get_ready_nodes(self) -> list[ReasoningNode]:
        ready = []
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue

            deps_met = all(self.nodes[dep].status == NodeStatus.COMPLETED for dep in node.dependencies)
            if deps_met:
                ready.append(node)
        return ready

    def is_complete(self) -> bool:
        return all(node.status == NodeStatus.COMPLETED for node in self.nodes.values())
