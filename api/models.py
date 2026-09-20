from pydantic import BaseModel
from typing import Any, Literal, Optional


class NodePosition(BaseModel):
    x: float = 0
    y: float = 0


class Node(BaseModel):
    id: str
    type: Literal["credential", "extract", "transform", "load"]
    position: NodePosition = NodePosition()
    data: dict[str, Any] = {}


class Edge(BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None


class PipelineGraph(BaseModel):
    pipeline_name: str = "Untitled"
    nodes: list[Node] = []
    edges: list[Edge] = []


class PreviewRequest(BaseModel):
    graph: PipelineGraph
    node_id: str
