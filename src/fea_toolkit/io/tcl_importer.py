"""OpenSees Tcl model importer → :class:`SAPModelData`.

Parses the subset of OpenSees Tcl model commands used by the SAC benchmark
frames (and similar elastic plane frames) and lifts the 2-D model
(``-ndm 2 -ndf 3``) into the toolkit's planar-3-D convention — nodes in the
X–Z vertical plane with out-of-plane DOFs restrained (see ``docs/tcl_import.md``
and ``.clinerules`` §3.11).

Supported model commands: ``model``, ``node``, ``fix``, ``mass``, ``set``,
``geomTransf``, ``element elasticBeamColumn`` and
``pattern Plain { load … }``.  Analysis / solver commands are ignored
silently; anything model-like that is not understood raises a warning so a
dropped quantity is never silent.
"""

import warnings
from dataclasses import dataclass
from typing import Optional

from ..model.sap_data import (
    FrameElement,
    JointLoad,
    LoadPattern,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)

# Analysis / solver / setup commands that carry no model topology or load
# information — safe to skip without a warning.
_ANALYSIS_COMMANDS = {
    "wipe",
    "wipeAnalysis",
    "system",
    "constraints",
    "numberer",
    "test",
    "algorithm",
    "integrator",
    "analysis",
    "analyze",
    "recorder",
    "timeSeries",
    "loadConst",
    "setTime",
    "puts",
    "eigen",
    "print",
    "logFile",
}


@dataclass
class _ElasticBeamColumn:
    """Parsed ``element elasticBeamColumn`` record (2-D or 3-D)."""

    tag: int
    i_node: int
    j_node: int
    area: float
    e_mod: float
    inertia: float
    mass: Optional[float] = None  # optional ``-mass`` (mass per length)


def parse_tcl_model(tcl_text: str, units: Optional[dict] = None) -> SAPModelData:
    """Parse an OpenSees Tcl model script into :class:`SAPModelData`.

    Args:
        tcl_text: The Tcl model source (SAC-style elastic plane frame).
        units: Model units dict (``{"F": ..., "L": ..., "T": ...}``).  Defaults
            to SI (``N / m``).  The SAC frames use ``{"F": "kip", "L": "in",
            "T": "C"}``.

    Returns:
        A :class:`SAPModelData` ready for the Preprocessor → AnalysisBuilder
        pipeline.
    """
    if units is None:
        units = {"F": "N", "L": "m", "T": "C"}

    ndm = 2
    nodes: dict[int, tuple[float, float, float]] = {}
    fixity: dict[int, list[int]] = {}
    masses: dict[int, list[float]] = {}
    beams: list[_ElasticBeamColumn] = []
    load_patterns: dict[str, LoadPattern] = {}
    joint_loads: list[JointLoad] = []
    variables: dict[str, str] = {}

    current_pattern: Optional[str] = None
    in_block = False

    for raw_line in tcl_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if in_block and line == "}":
            in_block = False
            current_pattern = None
            continue

        if in_block:
            _parse_block_line(line, current_pattern, ndm, joint_loads, variables)
            # A compact body may attach its closing ``}`` to the final token of
            # the last command (``load … -1.0}``); the block-line parser strips
            # the brace, so close the block here or later model commands would
            # be mis-parsed as pattern body.
            if _has_unescaped_closing_brace(line):
                in_block = False
                current_pattern = None
            continue

        parts = line.split()
        cmd = parts[0]

        # Analysis / solver commands carry no model topology or load
        # information; skip them before variable substitution so a ``$var``
        # they contain (e.g. ``analyze $nSteps``) is never flagged as an
        # undefined reference.
        if cmd in _ANALYSIS_COMMANDS:
            continue

        # ``set`` defines a variable consumed later (``set E 29000`` then
        # ``element elasticBeamColumn … $E …``); store it before dispatch.
        if cmd == "set":
            _handle_set(parts, variables)
            continue

        # Resolve ``$name`` tokens from the variables seen so far, so numeric
        # element/node fields carry the value rather than a bare ``$E``.
        resolved = _substitute_variables(parts, variables)
        if resolved is None:
            continue
        parts = resolved
        cmd = parts[0]

        if cmd == "model":
            ndm = _option_int(parts, "-ndm", ndm)
        elif cmd == "node":
            nodes[int(parts[1])] = _lift_coords([float(x) for x in parts[2:]], ndm)
        elif cmd == "fix":
            fixity[int(parts[1])] = [int(x) for x in parts[2:]]
        elif cmd == "mass":
            masses[int(parts[1])] = [float(x) for x in parts[2:]]
        elif cmd == "geomTransf":
            pass  # the builder supplies its own geometric transformation
        elif cmd == "uniaxialMaterial":
            _warn_if_not_elastic(parts)
        elif cmd == "element":
            _parse_element(parts, beams, ndm)
        elif cmd == "pattern":
            if len(parts) < 3 or parts[1] != "Plain":
                warnings.warn(
                    f"Unsupported pattern type "
                    f"{parts[1] if len(parts) > 1 else '?'!r}: only 'Plain' "
                    "patterns are imported; its loads are dropped.",
                    stacklevel=2,
                )
                continue
            tag = int(parts[2])
            name = f"P{tag}"
            load_patterns[name] = LoadPattern(name=name, pattern_type="Other", self_weight_factor=0)
            current_pattern = name
            in_block = _parse_inline_pattern_block(parts, name, ndm, joint_loads, variables)
        else:
            warnings.warn(f"Tcl command not imported, skipped: {line}", stacklevel=2)

    return _build_model(nodes, fixity, masses, beams, load_patterns, joint_loads, ndm, units)


def _option_int(parts: list[str], flag: str, default: int) -> int:
    for i, tok in enumerate(parts):
        if tok == flag and i + 1 < len(parts):
            return int(parts[i + 1])
    return default


def _lift_coords(coords: list[float], ndm: int) -> tuple[float, float, float]:
    if ndm == 2:
        return (coords[0], 0.0, coords[1])  # (x, y) → (X, Z), out-of-plane = 0
    return (coords[0], coords[1], coords[2])


def _warn_if_not_elastic(parts: list[str]) -> None:
    if len(parts) < 3 or parts[1] != "Elastic":
        warnings.warn(
            f"Only uniaxialMaterial Elastic is imported; skipped: {' '.join(parts)}",
            stacklevel=2,
        )


def _parse_element(parts: list[str], beams: list[_ElasticBeamColumn], ndm: int) -> None:
    if len(parts) < 2 or parts[1] != "elasticBeamColumn":
        warnings.warn(
            f"Only element elasticBeamColumn is imported; skipped: {' '.join(parts)}",
            stacklevel=2,
        )
        return
    if ndm == 3:
        # The 3-D layout ``… $A $E $G $J $Iy $Iz $transfTag`` carries a torsional
        # inertia (``$J``) where a 2-D parse would read the bending inertia; the
        # importer's isotropic single-inertia section cannot represent it, so
        # reject rather than mis-read ``$J`` as bending inertia.
        warnings.warn(
            "element elasticBeamColumn in a 3-D model is not imported (its "
            "3-D inertia layout is unsupported); skipped: " + " ".join(parts),
            stacklevel=2,
        )
        return
    if len(parts) < 8:
        warnings.warn(
            "Malformed element elasticBeamColumn skipped: " + " ".join(parts),
            stacklevel=2,
        )
        return
    mass = None
    if "-mass" in parts:
        idx = parts.index("-mass")
        if idx + 1 < len(parts):
            mass = float(parts[idx + 1])
        else:
            warnings.warn(
                "element elasticBeamColumn has '-mass' without a value; mass "
                "dropped: " + " ".join(parts),
                stacklevel=2,
            )
    beams.append(
        _ElasticBeamColumn(
            tag=int(parts[2]),
            i_node=int(parts[3]),
            j_node=int(parts[4]),
            area=float(parts[5]),
            e_mod=float(parts[6]),
            inertia=float(parts[7]),
            mass=mass,
        )
    )


def _split_braces(tokens: list[str]) -> list[str]:
    """Split ``{``/``}`` out of tokens so brace delimiters stand alone.

    Compact Tcl brace bodies attach the delimiters to adjacent tokens
    (``{load``, ``-1.0}``); the inline-pattern and block-line parsers need
    them as standalone tokens to locate the body boundaries.  Brace-valued
    ``set`` arguments are unaffected — this runs only on pattern/block token
    streams, never on a ``set`` value.

    Args:
        tokens: The whitespace-split words of a single command (or command
            fragment), in source order.

    Returns:
        A new token list in which every ``{`` or ``}`` character that was
        attached to a token has been split into its own standalone token,
        preserving the original token order.
    """
    out: list[str] = []
    for tok in tokens:
        if "{" in tok or "}" in tok:
            piece: list[str] = []
            for ch in tok:
                if ch in "{}":
                    if piece:
                        out.append("".join(piece))
                        piece = []
                    out.append(ch)
                else:
                    piece.append(ch)
            if piece:
                out.append("".join(piece))
        else:
            out.append(tok)
    return out


def _has_unescaped_closing_brace(line: str) -> bool:
    """True when *line* contains a ``}`` not escaped by a backslash.

    A closing brace preceded by an odd number of backslashes is escaped
    (``\\}``) and does not close a pattern block; an even number (including
    zero) is a real delimiter.
    """
    for i, ch in enumerate(line):
        if ch != "}":
            continue
        backslashes = 0
        j = i - 1
        while j >= 0 and line[j] == "\\":
            backslashes += 1
            j -= 1
        if backslashes % 2 == 0:
            return True
    return False


def _parse_block_line(
    line: str,
    pattern_name: Optional[str],
    ndm: int,
    joint_loads: list[JointLoad],
    variables: Optional[dict[str, str]] = None,
) -> None:
    parts = [p for p in _split_braces(line.split()) if p not in ("{", "}")]
    if variables is not None:
        resolved = _substitute_variables(parts, variables)
        if resolved is None:
            return
        parts = resolved
    if not parts:
        return
    if parts[0] == "load":
        node = int(parts[1])
        vals = [float(x) for x in parts[2:]]
        joint_loads.append(_lift_joint_load(pattern_name or "", node, vals, ndm))
    elif parts[0] == "eleLoad":
        warnings.warn(
            f"eleLoad is not imported; distributed member loads dropped: {line}",
            stacklevel=2,
        )
    else:
        warnings.warn(f"Unknown pattern command skipped: {line}", stacklevel=2)


def _handle_set(parts: list[str], variables: dict[str, str]) -> None:
    """Store a ``set name value`` variable for later ``$name`` substitution.

    Only literal values and ``$other`` references are supported; a bracketed
    expression is rejected with an explicit unsupported-syntax warning rather
    than being imported as an opaque string.
    """
    if len(parts) < 3:
        return
    name = parts[1]
    value = parts[2]
    if value.startswith("$") and len(value) > 1:
        ref = value[1:]
        if ref in variables:
            variables[name] = variables[ref]
        else:
            warnings.warn(
                f"Unsupported variable substitution {value!r} in set {name!r}: "
                f"{ref!r} is not defined.",
                stacklevel=2,
            )
        return
    if "[" in value or "]" in value or "{" in value or "}" in value:
        warnings.warn(
            f"Unsupported set expression {value!r} for variable {name!r}; only "
            "literal values are imported.",
            stacklevel=2,
        )
        return
    variables[name] = value


def _substitute_variables(parts: list[str], variables: dict[str, str]) -> Optional[list[str]]:
    """Resolve ``$name`` tokens in *parts* from *variables*.

    Returns ``None`` (after warning) when a token references an undefined
    variable, so the caller can skip the command instead of letting a numeric
    parse fail.
    """
    out: list[str] = []
    for tok in parts:
        if tok.startswith("$") and len(tok) > 1:
            name = tok[1:]
            if name in variables:
                out.append(variables[name])
            else:
                warnings.warn(
                    f"Unsupported variable substitution {tok!r}: {name!r} is not "
                    "defined; skipping this command.",
                    stacklevel=2,
                )
                return None
        else:
            out.append(tok)
    return out


_BLOCK_COMMANDS = ("load", "eleLoad", "sp")


def _parse_inline_pattern_block(
    parts: list[str],
    pattern_name: str,
    ndm: int,
    joint_loads: list[JointLoad],
    variables: dict[str, str],
) -> bool:
    """Handle a ``pattern … { load … }`` command whose brace body may be inline.

    When the body opens and closes on the same physical line, its commands are
    parsed immediately and ``False`` is returned (the block is complete);
    otherwise ``True`` is returned so the line loop keeps reading the body on
    following lines.
    """
    parts = _split_braces(parts)
    if "{" not in parts:
        return True
    open_idx = parts.index("{")
    rest = parts[open_idx + 1 :]
    if "}" not in rest:
        # The brace body continues on following lines, but any commands on the
        # opening line after ``{`` must still be parsed rather than dropped.
        _parse_block_commands(rest, pattern_name, ndm, joint_loads, variables)
        return True
    body = rest[: rest.index("}")]
    _parse_block_commands(body, pattern_name, ndm, joint_loads, variables)
    return False


def _parse_block_commands(
    tokens: list[str],
    pattern_name: str,
    ndm: int,
    joint_loads: list[JointLoad],
    variables: dict[str, str],
) -> None:
    """Parse the commands of an inline brace body (between ``{`` and ``}``)."""
    command: list[str] = []
    for tok in tokens:
        if tok in _BLOCK_COMMANDS and command:
            _parse_block_line(" ".join(command), pattern_name, ndm, joint_loads, variables)
            command = [tok]
        else:
            command.append(tok)
    if command:
        _parse_block_line(" ".join(command), pattern_name, ndm, joint_loads, variables)


def _lift_joint_load(pattern: str, node: int, vals: list[float], ndm: int) -> JointLoad:
    if ndm == 2:  # [Fx, Fy, Mz] → [fx, fz, my]; +Mz → −My (out-of-plane axis).
        fx, fy, mz = [*vals, 0.0, 0.0, 0.0][:3]
        return JointLoad(pattern=pattern, node_id=str(node), fx=fx, fz=fy, my=-mz)
    fx, fy, fz, mx, my, mz = (vals + [0.0] * 6)[:6]
    return JointLoad(pattern=pattern, node_id=str(node), fx=fx, fy=fy, fz=fz, mx=mx, my=my, mz=mz)


def _lift_mass_scalar(vals: list[float], ndm: int) -> float:
    if ndm == 2:
        mx, my = [*vals, 0.0, 0.0][:2]
        if abs(mx - my) > 1e-12:
            warnings.warn(
                f"Directional masses differ ({mx} vs {my}); using the X mass", stacklevel=2
            )
        return mx
    return vals[0] if vals else 0.0


def _build_model(
    nodes: dict[int, tuple[float, float, float]],
    fixity: dict[int, list[int]],
    masses: dict[int, list[float]],
    beams: list[_ElasticBeamColumn],
    load_patterns: dict[str, LoadPattern],
    joint_loads: list[JointLoad],
    ndm: int,
    units: dict,
) -> SAPModelData:
    model_nodes = {
        str(tag): Node(node_id=str(tag), node_tag=tag, x=x, y=y, z=z)
        for tag, (x, y, z) in nodes.items()
    }

    restraints: dict[str, Restraint] = {}
    for tag in nodes:
        if ndm == 2:
            fx = [*fixity.get(tag, []), 0, 0, 0][:3]
            # [UX, UY, RZ] → [UX, UZ, RY], out-of-plane always restrained.
            restraints[str(tag)] = Restraint([fx[0], 1, fx[1], 1, fx[2], 1])
        else:
            f = [*fixity.get(tag, []), 0, 0, 0, 0, 0, 0][:6]
            restraints[str(tag)] = Restraint([int(v) for v in f])

    materials: dict[str, Material] = {}
    sections: dict[str, Section] = {}
    frame_elements: dict[str, FrameElement] = {}
    frame_assignments: dict[str, str] = {}
    mat_by_e: dict[float, str] = {}
    sec_by_key: dict[tuple, str] = {}
    for b in beams:
        e_key = round(b.e_mod, 8)
        mname = mat_by_e.get(e_key)
        if mname is None:
            mname = f"E{b.e_mod:g}"
            materials[mname] = Material(
                name=mname,
                type="Steel",
                E_mod=b.e_mod,
                G_mod=b.e_mod / 2.6,
                nu=0.3,
                unit_weight=0.0,
                Fy=1e30,  # elastic — yield is never reached
            )
            mat_by_e[e_key] = mname
        s_key = (b.area, e_key, b.inertia)
        sname = sec_by_key.get(s_key)
        if sname is None:
            sname = f"SEC{len(sections) + 1}"
            sections[sname] = Section(
                name=sname,
                shape="I/Wide Flange",
                material=mname,
                A=b.area,
                I33=b.inertia,
                I22=b.inertia,
                J=b.inertia,
            )
            sec_by_key[s_key] = sname
        frame_elements[str(b.tag)] = FrameElement(
            elem_id=str(b.tag), elem_tag=b.tag, node_i=str(b.i_node), node_j=str(b.j_node)
        )
        frame_assignments[str(b.tag)] = sname
        if b.mass is not None:
            warnings.warn(
                f"element elasticBeamColumn {b.tag} carries a -mass value "
                f"({b.mass}) that the model cannot represent; the distributed "
                "mass was dropped.",
                stacklevel=2,
            )

    added_masses = {str(tag): _lift_mass_scalar(vals, ndm) for tag, vals in masses.items()}
    mass_sources: dict[str, MassSource] = {}
    if added_masses:
        mass_sources["MSSSRC1"] = MassSource(name="MSSSRC1", masses=True)

    return SAPModelData(
        nodes=model_nodes,
        restraints=restraints,
        materials=materials,
        sections=sections,
        frame_elements=frame_elements,
        area_elements={},
        frame_assignments=frame_assignments,
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        load_patterns=load_patterns,
        frame_dist_loads=[],
        joint_loads=joint_loads,
        added_masses=added_masses,
        mass_sources=mass_sources,
        units=units,
    )
