"""
Blueprint-style function system data models
- DataType: pin data types with colors and compatibility
- NodeType: all internal node types (22 types, 4 categories)
- FunctionNode: internal node data
- FunctionEdge: internal edge data (exec or data)
- FunctionParameter: function parameter definition
- FunctionDefinition: complete function definition (v2)
"""
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any, Tuple


# ──────────────────────────────────────────
# Data Types
# ──────────────────────────────────────────

class DataType(str, Enum):
    EXEC = "exec"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    IMAGE = "image"
    ARRAY = "array"
    OBJECT = "object"
    ANY = "any"


DATA_TYPE_COLORS: Dict[DataType, str] = {
    DataType.EXEC: "#cccccc",
    DataType.STRING: "#4a9eff",
    DataType.NUMBER: "#27ae60",
    DataType.BOOLEAN: "#c0392b",
    DataType.IMAGE: "#e67e22",
    DataType.ARRAY: "#8e44ad",
    DataType.OBJECT: "#f39c12",
    DataType.ANY: "#95a5a6",
}

# Conversion compatibility: (from_type, to_type) -> can convert
_DATA_TYPE_COMPAT: Dict[Tuple[DataType, DataType], bool] = {
    (DataType.NUMBER, DataType.STRING): True,
    (DataType.BOOLEAN, DataType.STRING): True,
    (DataType.NUMBER, DataType.BOOLEAN): True,
    (DataType.STRING, DataType.NUMBER): True,
    (DataType.STRING, DataType.BOOLEAN): True,
    # F4: 누락된 변환 추가
    (DataType.ARRAY, DataType.STRING): True,
    (DataType.STRING, DataType.ARRAY): True,
    (DataType.OBJECT, DataType.STRING): True,
    (DataType.STRING, DataType.OBJECT): True,
    (DataType.BOOLEAN, DataType.NUMBER): True,
}


def can_convert(from_type: DataType, to_type: DataType) -> bool:
    """Check if from_type can connect to to_type"""
    if from_type == to_type:
        return True
    if from_type == DataType.ANY or to_type == DataType.ANY:
        return True
    return _DATA_TYPE_COMPAT.get((from_type, to_type), False)


# ──────────────────────────────────────────
# Node Types
# ──────────────────────────────────────────

class NodeType(str, Enum):
    # Control Flow
    START = "start"
    END = "end"
    BRANCH = "branch"
    SWITCH = "switch"
    FOR_EACH = "for_each"
    WHILE_LOOP = "while_loop"
    SEQUENCE = "sequence"
    # AI
    LLM_CALL = "llm_call"
    PROMPT_BUILDER = "prompt_builder"
    RESPONSE_PARSER = "response_parser"
    IMAGE_GENERATOR = "image_generator"
    # Data Processing
    MATH = "math"
    COMPARE = "compare"
    STRING_OP = "string_op"
    ARRAY_OP = "array_op"
    JSON_PARSE = "json_parse"
    JSON_PATH = "json_path"
    TYPE_CONVERT = "type_convert"
    # Variables
    GET_VARIABLE = "get_variable"
    SET_VARIABLE = "set_variable"
    MAKE_LITERAL = "make_literal"


# Category colors for node title bars
CATEGORY_COLORS = {
    "control_flow": "#27ae60",
    "ai": "#2980b9",
    "data": "#8e44ad",
    "variables": "#e67e22",
}

# Node type -> category mapping
NODE_CATEGORIES: Dict[str, str] = {
    NodeType.START: "control_flow",
    NodeType.END: "control_flow",
    NodeType.BRANCH: "control_flow",
    NodeType.SWITCH: "control_flow",
    NodeType.FOR_EACH: "control_flow",
    NodeType.WHILE_LOOP: "control_flow",
    NodeType.SEQUENCE: "control_flow",
    NodeType.LLM_CALL: "ai",
    NodeType.PROMPT_BUILDER: "ai",
    NodeType.RESPONSE_PARSER: "ai",
    NodeType.IMAGE_GENERATOR: "ai",
    NodeType.MATH: "data",
    NodeType.COMPARE: "data",
    NodeType.STRING_OP: "data",
    NodeType.ARRAY_OP: "data",
    NodeType.JSON_PARSE: "data",
    NodeType.JSON_PATH: "data",
    NodeType.TYPE_CONVERT: "data",
    NodeType.GET_VARIABLE: "variables",
    NodeType.SET_VARIABLE: "variables",
    NodeType.MAKE_LITERAL: "variables",
}


# ──────────────────────────────────────────
# Port Definition
# ──────────────────────────────────────────

@dataclass
class PortDef:
    """Port definition for node types"""
    port_id: str
    data_type: DataType
    label: str


# ──────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────

@dataclass
class FunctionNode:
    """Internal node in a function graph"""
    node_id: str
    node_type: str  # NodeType value
    x: float = 0.0
    y: float = 0.0
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FunctionEdge:
    """Internal edge connecting two ports"""
    edge_id: str
    source_node_id: str
    source_port_id: str
    target_node_id: str
    target_port_id: str
    edge_type: str = "data"  # "exec" or "data"


@dataclass
class FunctionParameter:
    """Function parameter definition"""
    name: str
    param_type: str = "string"  # DataType value


@dataclass
class FunctionOutput:
    """Function output definition (extracted from End nodes)"""
    name: str
    node_id: str
    color: str = "#4a9eff"


@dataclass
class FunctionDefinition:
    """Complete function definition (v2 = Blueprint style)"""
    function_id: str
    name: str
    description: str = ""
    version: int = 2
    color: str = "#e67e22"
    nodes: List[FunctionNode] = field(default_factory=list)
    edges: List[FunctionEdge] = field(default_factory=list)
    parameters: List[FunctionParameter] = field(default_factory=list)
    variables: List[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "function_id": self.function_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "color": self.color,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "x": n.x,
                    "y": n.y,
                    "config": n.config,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "edge_id": e.edge_id,
                    "source_node_id": e.source_node_id,
                    "source_port_id": e.source_port_id,
                    "target_node_id": e.target_node_id,
                    "target_port_id": e.target_port_id,
                    "edge_type": e.edge_type,
                }
                for e in self.edges
            ],
            "parameters": [
                {"name": p.name, "param_type": p.param_type}
                for p in self.parameters
            ],
            "variables": list(self.variables),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FunctionDefinition":
        nodes = [
            FunctionNode(
                node_id=n["node_id"],
                node_type=n["node_type"],
                x=n.get("x", 0.0),
                y=n.get("y", 0.0),
                config=n.get("config", {}),
            )
            for n in data.get("nodes", [])
        ]
        edges = [
            FunctionEdge(
                edge_id=e["edge_id"],
                source_node_id=e["source_node_id"],
                source_port_id=e["source_port_id"],
                target_node_id=e["target_node_id"],
                target_port_id=e["target_port_id"],
                edge_type=e.get("edge_type", "data"),
            )
            for e in data.get("edges", [])
        ]
        parameters = [
            FunctionParameter(
                name=p["name"],
                param_type=p.get("param_type", "string"),
            )
            for p in data.get("parameters", [])
        ]
        return cls(
            function_id=data["function_id"],
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", 2),
            color=data.get("color", "#e67e22"),
            nodes=nodes,
            edges=edges,
            parameters=parameters,
            variables=data.get("variables", []),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
        )

    def get_outputs(self) -> List[FunctionOutput]:
        """Extract output list from End nodes"""
        outputs = []
        for node in self.nodes:
            if node.node_type == NodeType.END:
                name = node.config.get("output_name", "output")
                color = node.config.get("port_color", "#4a9eff")
                outputs.append(FunctionOutput(name=name, node_id=node.node_id, color=color))
        return outputs

    @classmethod
    def create_default(cls, name: str = "New Function") -> "FunctionDefinition":
        """Create a default function with Start + End nodes"""
        start = FunctionNode(
            node_id=str(uuid.uuid4()),
            node_type=NodeType.START,
            x=-150,
            y=0,
        )
        end = FunctionNode(
            node_id=str(uuid.uuid4()),
            node_type=NodeType.END,
            x=200,
            y=0,
            config={"output_name": "output"},
        )
        edge = FunctionEdge(
            edge_id=str(uuid.uuid4()),
            source_node_id=start.node_id,
            source_port_id="exec_out",
            target_node_id=end.node_id,
            target_port_id="exec_in",
            edge_type="exec",
        )
        return cls(
            function_id=str(uuid.uuid4()),
            name=name,
            nodes=[start, end],
            edges=[edge],
            created_at=time.time(),
            updated_at=time.time(),
        )
