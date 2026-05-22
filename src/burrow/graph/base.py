from typing import Dict, List, Any, Optional, Set

class Node:
    """Represents a node in the dependency/error graph."""
    def __init__(self, node_id: str, node_type: str, properties: Optional[Dict[str, Any]] = None):
        self.id = node_id
        self.type = node_type
        self.properties = properties or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "properties": self.properties
        }


class Edge:
    """Represents a directed link between two nodes in the graph."""
    def __init__(self, source: str, target: str, relation_type: str, properties: Optional[Dict[str, Any]] = None):
        self.source = source
        self.target = target
        self.type = relation_type
        self.properties = properties or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "properties": self.properties
        }


class Graph:
    """Simple in-memory directed graph to model code structures and propagation paths."""
    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self._adjacency: Dict[str, Set[str]] = {}

    def add_node(self, node_id: str, node_type: str, properties: Optional[Dict[str, Any]] = None) -> Node:
        """Adds or updates a node in the graph."""
        if node_id not in self.nodes:
            self.nodes[node_id] = Node(node_id, node_type, properties)
        else:
            if properties:
                self.nodes[node_id].properties.update(properties)
        return self.nodes[node_id]

    def add_edge(self, source: str, target: str, relation_type: str, properties: Optional[Dict[str, Any]] = None) -> Edge:
        """Adds a directed edge between source and target nodes."""
        # Ensure nodes exist
        self.add_node(source, "unknown")
        self.add_node(target, "unknown")
        
        edge = Edge(source, target, relation_type, properties)
        self.edges.append(edge)
        if source not in self._adjacency:
            self._adjacency[source] = set()
        self._adjacency[source].add(target)
        return edge

    def get_node(self, node_id: str) -> Optional[Node]:
        """Gets a node by its ID."""
        return self.nodes.get(node_id)

    def get_successors(self, node_id: str) -> List[str]:
        """Gets target nodes connected from the source node."""
        return list(self._adjacency.get(node_id, set()))

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the graph into a standard dictionary format."""
        return {
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "edges": [edge.to_dict() for edge in self.edges]
        }
