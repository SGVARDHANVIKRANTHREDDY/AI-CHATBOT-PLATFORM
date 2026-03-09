from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskNode(BaseModel):
    """Represents a single step in the agentic plan."""

    id: str
    agent: str  # e.g., "research_agent", "reasoning_agent", "coding_agent"
    task: str
    dependencies: list[str] = Field(default_factory=list)
    result: Any | None = None
    status: str = "pending"  # pending, running, completed, failed


class TaskGraph(BaseModel):
    """
    A Directed Acyclic Graph (DAG) for task orchestration.
    """

    nodes: dict[str, TaskNode] = Field(default_factory=dict)

    def add_node(self, node: TaskNode):
        self.nodes[node.id] = node

    def get_ready_tasks(self) -> list[TaskNode]:
        """Returns tasks whose dependencies are all completed."""
        ready = []
        for node in self.nodes.values():
            if node.status != "pending":
                continue

            deps_met = all(self.nodes[dep].status == "completed" for dep in node.dependencies)
            if deps_met:
                ready.append(node)
        return ready

    def mark_completed(self, node_id: str, result: Any):
        if node_id in self.nodes:
            self.nodes[node_id].status = "completed"
            self.nodes[node_id].result = result

    def is_complete(self) -> bool:
        return all(node.status == "completed" for node in self.nodes.values())

    def to_dict(self) -> dict[str, Any]:
        return {nid: node.model_dump() for nid, node in self.nodes.items()}
