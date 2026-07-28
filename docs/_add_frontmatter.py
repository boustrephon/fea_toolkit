#!/usr/bin/env python3
"""One-time script to add YAML frontmatter to all docs/*.md files.

Scans each file, detects whether frontmatter already exists (starts with ---),
and if not, prepends the appropriate YAML based on the file's content.

Safe to re-run: will not double-add frontmatter.
"""

import os
import re
from pathlib import Path

DOCS_DIR = Path(__file__).parent

# ── Frontmatter definitions ──────────────────────────────────────

FRONTMATTER: dict[str, dict] = {
    "analysis_builder_migration_plan.md": {
        "title": "AnalysisBuilder Migration Plan",
        "tags": ["architecture", "migration", "planning"],
        "category": ["planning"],
        "status": "draft",
        "description": "Migration plan for the two-stage pipeline, superseding monolithic OpenSeesBuilder.",
        "related": ["layered_analysis_workflow.md", "workflow.md", "dev_notes.md"],
    },
    "builder_reference.md": {
        "title": "Builder Reference — Two-stage Pipeline",
        "tags": ["architecture", "preprocessor", "analysis-builder", "reference"],
        "category": ["core-pipeline"],
        "status": "complete",
        "description": "Reference for the Preprocessor + AnalysisBuilder two-stage architecture.",
        "related": ["workflow.md", "layered_analysis_workflow.md", "element_classification.md"],
    },
    "constraint_detection.md": {
        "title": "Constraint Edge Detection",
        "tags": ["shell", "meshing", "constraints", "edge-detection"],
        "category": ["model-features"],
        "status": "complete",
        "description": "Detecting and applying edge constraints for shell element connectivity.",
        "related": ["shell_support.md", "element_splitting.md", "builder_reference.md"],
    },
    "dev_notes.md": {
        "title": "Development Notes",
        "tags": ["architecture", "development", "notes"],
        "category": ["planning"],
        "status": "draft",
        "description": "Miscellaneous development notes, design decisions, and technical context.",
        "related": ["analysis_builder_migration_plan.md"],
    },
    "element_classification.md": {
        "title": "Element Classification",
        "tags": ["elements", "classification", "beams", "columns", "braces", "walls", "slabs"],
        "category": ["model-features"],
        "status": "complete",
        "description": "How frame and area elements are classified into structural roles (beam, column, brace, wall, slab).",
        "related": ["element_splitting.md", "stiffness_factors.md", "element_properties_config.md", "builder_reference.md"],
    },
    "element_properties_config.md": {
        "title": "Element Properties Configuration",
        "tags": ["config", "element-properties", "fiber", "hinges", "nonlinear", "shell"],
        "category": ["model-features"],
        "status": "complete",
        "description": "Config-driven per-element creation property system: integration rules, fiber sections, hinges, layered shells.",
        "related": ["element_classification.md", "pushover_analysis.md", "shell_support.md", "layered_analysis_workflow.md"],
    },
    "element_splitting.md": {
        "title": "Element Splitting",
        "tags": ["elements", "splitting", "meshing", "load-redistribution"],
        "category": ["model-features"],
        "status": "complete",
        "description": "Splitting frame elements at joints and intersections with parent-child tracking and load redistribution.",
        "related": ["element_classification.md", "builder_reference.md", "workflow.md"],
    },
    "layered_analysis_workflow.md": {
        "title": "Layered Analysis Workflow for the v3 Architecture",
        "tags": ["architecture", "workflow", "preprocessor", "analysis-builder", "mesh-model"],
        "category": ["core-pipeline"],
        "status": "complete",
        "description": "Detailed design of the v3 architecture: Preprocessor, MeshModel, AnalysisBuilder pipeline.",
        "related": ["workflow.md", "builder_reference.md", "analysis_builder_migration_plan.md"],
    },
    "modal_analysis.md": {
        "title": "Modal Analysis Options",
        "tags": ["analysis-type", "modal", "eigen", "eigenvalue", "solver"],
        "category": ["analysis-types"],
        "status": "complete",
        "description": "Modal analysis solver options, mode shape visualisation, and usage examples.",
        "related": ["pushover_analysis.md", "report_generation.md", "storey_response.md"],
    },
    "pushover_analysis.md": {
        "title": "Pushover (Non-linear Static) Analysis",
        "tags": ["analysis-type", "pushover", "nonlinear", "fiber", "hinges", "brace", "csm"],
        "category": ["analysis-types"],
        "status": "complete",
        "description": "Pushover analysis: fiber sections, lumped hinges, brace buckling approaches, ADRS conversion, and CSM.",
        "related": ["modal_analysis.md", "tcl_export.md", "stiffness_factors.md", "element_properties_config.md", "storey_response.md"],
    },
    "report_generation.md": {
        "title": "Report Generation — Design Proposal",
        "tags": ["reporting", "design-proposal", "yaml-config", "hdf5"],
        "category": ["analysis-types"],
        "status": "draft",
        "description": "Design proposal for a YAML-driven report generation pipeline with HDF5 storage.",
        "related": ["results_schema.md", "pushover_analysis.md", "modal_analysis.md"],
    },
    "results_schema.md": {
        "title": "Unified Results Schema — Design Proposal",
        "tags": ["schema", "npz", "results", "design-proposal", "io"],
        "category": ["export-viz"],
        "status": "draft",
        "description": "Design proposal for the unified NPZ results schema, the canonical on-disk exchange format.",
        "related": ["report_generation.md", "viewer.md", "rhino_export.md", "storey_response.md"],
    },
    "rhino_export.md": {
        "title": "Rhino 3-D Export",
        "tags": ["export", "rhino", "visualisation", "geometry"],
        "category": ["export-viz"],
        "status": "complete",
        "description": "Export to Rhino 8: centreline and extrusion geometry, layers, colours, and Grasshopper metadata.",
        "related": ["viewer.md", "results_schema.md", "tcl_export.md"],
    },
    "shell_support.md": {
        "title": "Shell Element Support",
        "tags": ["shell", "area-elements", "meshing", "elements"],
        "category": ["model-features"],
        "status": "complete",
        "description": "Shell element types, meshing strategies, and layered shell support for nonlinear wall analysis.",
        "related": ["constraint_detection.md", "element_classification.md", "element_properties_config.md", "builder_reference.md"],
    },
    "stiffness_factors.md": {
        "title": "Per-type Stiffness Factors (ACI 318 Cracked-Section Simulation)",
        "tags": ["stiffness", "cracked-section", "aci-318", "modifiers"],
        "category": ["model-features"],
        "status": "complete",
        "description": "Applying ACI 318 cracked-section stiffness modifiers per structural role (beam, column, wall, slab).",
        "related": ["element_classification.md", "pushover_analysis.md", "modal_analysis.md"],
    },
    "storey_response.md": {
        "title": "Storey-level Response Methodology",
        "tags": ["analysis-type", "storey", "drift", "shear", "displacement", "post-processing"],
        "category": ["analysis-types"],
        "status": "complete",
        "description": "Storey displacement, drift, shear, and modal drift extraction and visualisation.",
        "related": ["pushover_analysis.md", "modal_analysis.md", "results_schema.md"],
    },
    "tcl_export.md": {
        "title": "Tcl Export for Nonlinear Analysis",
        "tags": ["export", "tcl", "xara", "opensees", "scripting"],
        "category": ["export-viz"],
        "status": "complete",
        "description": "Exporting models to standalone OpenSees Tcl scripts for nonlinear analysis and Xara/OpenSeesRT runtime.",
        "related": ["xara_tcl_runtime_guide.md", "xara_pushover_workflow.md", "xara_gravity_and_solver.md", "pushover_analysis.md"],
    },
    "viewer.md": {
        "title": "Visualisation Toolkit",
        "tags": ["visualisation", "pyvista", "viewer", "html-export", "interactive"],
        "category": ["export-viz"],
        "status": "complete",
        "description": "PyVista-based 3D viewer, backend-agnostic renderer, interactive browser viewer, and HTML export.",
        "related": ["results_schema.md", "rhino_export.md", "report_generation.md"],
    },
    "workflow.md": {
        "title": "Analysis Workflow",
        "tags": ["architecture", "workflow", "pipeline", "end-to-end"],
        "category": ["core-pipeline"],
        "status": "complete",
        "description": "End-to-end analysis pipeline: parsing, preprocessing, domain construction, analysis execution, post-processing.",
        "related": ["builder_reference.md", "layered_analysis_workflow.md", "element_classification.md", "element_splitting.md"],
    },
    "xara_gravity_and_solver.md": {
        "title": "Xara/OpenSeesRT Gravity & Solver Lessons Learned",
        "tags": ["xara", "tcl", "gravity", "solver", "lessons-learned"],
        "category": ["tool-specific"],
        "status": "complete",
        "description": "Lessons learned from Xara/OpenSeesRT gravity analysis and solver configuration.",
        "related": ["xara_tcl_runtime_guide.md", "xara_pushover_workflow.md", "tcl_export.md"],
    },
    "xara_pushover_workflow.md": {
        "title": "Xara Pushover Workflow — Nonlinear Analysis via Tcl",
        "tags": ["xara", "tcl", "pushover", "nonlinear", "workflow"],
        "category": ["tool-specific"],
        "status": "complete",
        "description": "Pushover analysis workflow for Xara/OpenSeesRT via Tcl script generation and execution.",
        "related": ["xara_gravity_and_solver.md", "xara_tcl_runtime_guide.md", "tcl_export.md", "pushover_analysis.md"],
    },
    "xara_tcl_runtime_guide.md": {
        "title": "Xara/OpenSeesRT Tcl Runtime Guide",
        "tags": ["xara", "tcl", "runtime", "openseesrt", "execution"],
        "category": ["tool-specific"],
        "status": "complete",
        "description": "Guide to running OpenSees Tcl scripts with Xara/OpenSeesRT: execution, monitoring, and troubleshooting.",
        "related": ["tcl_export.md", "xara_pushover_workflow.md", "xara_gravity_and_solver.md"],
    },
}


def format_frontmatter(fm: dict) -> str:
    """Render a dict as YAML-style frontmatter."""
    lines = ["---"]
    for key in ("title", "description", "status"):
        if key in fm:
            val = fm[key]
            lines.append(f'{key}: "{val}"')
    if "tags" in fm:
        lines.append(f"tags: [{', '.join(fm['tags'])}]")
    if "category" in fm:
        lines.append(f"category: [{', '.join(fm['category'])}]")
    if "related" in fm:
        lines.append(f"related: [{', '.join(fm['related'])}]")
    lines.append("---")
    return "\n".join(lines) + "\n"


def process_file(path: Path) -> bool:
    """Add frontmatter if missing. Returns True if modified."""
    fname = path.name
    if fname not in FRONTMATTER:
        print(f"  ⚠ No frontmatter defined for {fname}, skipping")
        return False

    content = path.read_text(encoding="utf-8")
    if content.startswith("---"):
        print(f"  ℹ {fname} — frontmatter already exists, skipping")
        return False

    fm = format_frontmatter(FRONTMATTER[fname])
    new_content = fm + content
    path.write_text(new_content, encoding="utf-8")
    print(f"  ✔ {fname} — added frontmatter")
    return True


def main():
    md_files = sorted(DOCS_DIR.glob("*.md"))
    modded = 0
    skipped = 0
    for f in md_files:
        if f.name == "README.md" or f.name.startswith("_"):
            continue
        if process_file(f):
            modded += 1
        else:
            skipped += 1
    print(f"\nDone: {modded} modified, {skipped} skipped")


if __name__ == "__main__":
    main()