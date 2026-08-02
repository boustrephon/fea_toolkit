"""AnalysisManager — dependency-resolving pipeline executor.

Orchestrates a collection of :class:`~fea_toolkit.analysis.base.Analysis`
objects, respecting their ``requires`` declarations to determine
execution order and passing results between dependent analyses.
"""

from typing import TYPE_CHECKING, Optional

from fea_toolkit.analysis.base import Analysis, AnalysisResult

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


class AnalysisManager:
    """Orchestrate a list of analyses with dependency resolution.

    Parameters
    ----------
    mesh_model : MeshModel
        Pre-processed topology shared by all analyses.
    out_dir : str, optional
        Output directory for cached results and file-backed storage.
    """

    def __init__(self, mesh_model: "MeshModel", out_dir: Optional[str] = None):
        self.mesh_model = mesh_model
        self.out_dir = out_dir
        self._analyses: list[Analysis] = []
        self.results: dict[str, AnalysisResult] = {}

    def add(self, analysis: Analysis) -> "AnalysisManager":
        """Register an analysis to run. Returns self for chaining."""
        self._analyses.append(analysis)
        return self

    def run_all(self) -> dict[str, AnalysisResult]:
        """Execute all registered analyses in dependency order.

        Uses Kahn's algorithm to topologically sort based on each
        analysis's ``requires`` property.  Analyses with unmet
        dependencies raise a ``ValueError``.

        Returns
        -------
        dict
            ``{analysis.name: AnalysisResult, ...}``
        """
        ordered = self._topological_sort()
        for analysis in ordered:
            # Wire in results from completed dependencies
            self._inject_dependencies(analysis)
            result = analysis.run()
            self.results[analysis.name] = result
        return self.results

    def _inject_dependencies(self, analysis: Analysis) -> None:
        """Pass completed results to analyses that need them.

        Currently supports:
        - ModalAnalysis → ResponseSpectrumAnalysis (via ``modal_result``)
        - ModalAnalysis → PushoverAnalysis (via ``modal_result``)
        """
        for dep_type in analysis.requires:
            dep_name = None
            for name, result in self.results.items():
                if result.analysis_type == dep_type.__name__:
                    dep_name = name
                    break
            if dep_name is None:
                raise ValueError(
                    f"{type(analysis).__name__} requires "
                    f"{dep_type.__name__} but none was registered"
                )
            # Check if the analysis has a _modal_result attribute
            # (ResponseSpectrumAnalysis and PushoverAnalysis both do)
            if getattr(analysis, "_modal_result", None) is None:
                analysis._modal_result = self.results[dep_name]

    def _topological_sort(self) -> list[Analysis]:
        """Kahn's algorithm on the analysis dependency graph.

        Returns analyses in execution order.  Raises ``ValueError``
        if a circular dependency is detected.
        """
        # Build adjacency and in-degree maps
        remaining = {id(a) for a in self._analyses}
        edges: dict[int, list[int]] = {id(a): [] for a in self._analyses}
        in_degree: dict[int, int] = {id(a): 0 for a in self._analyses}

        id_map: dict[int, Analysis] = {id(a): a for a in self._analyses}

        for a in self._analyses:
            for dep_type in a.requires:
                # Find the analysis that provides this dependency
                for b in self._analyses:
                    if type(b) is dep_type or issubclass(type(b), dep_type):
                        dep_id = id(b)
                        edges[dep_id].append(id(a))
                        in_degree[id(a)] = in_degree.get(id(a), 0) + 1
                        break

        # Kahn's algorithm
        queue = [n for n in remaining if in_degree.get(n, 0) == 0]
        ordered: list[Analysis] = []

        while queue:
            node_id = queue.pop(0)
            remaining.discard(node_id)
            ordered.append(id_map[node_id])
            for neighbor in edges.get(node_id, []):
                if neighbor in remaining:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)

        if remaining:
            raise ValueError(
                f"Circular dependency detected among analyses: "
                f"{[id_map[n].name for n in remaining]}"
            )

        return ordered

    def run_one(self, analysis: Analysis) -> AnalysisResult:
        """Run a single analysis in isolation (no dependency resolution)."""
        self._inject_dependencies(analysis)
        result = analysis.run()
        self.results[analysis.name] = result
        return result
