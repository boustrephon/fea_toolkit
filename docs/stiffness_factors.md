# Per-type stiffness factors (ACI 318 cracked-section simulation)

The ``stiffness_factors`` config option applies different Young's modulus
reduction factors to structural element types, simulating cracked-section
stiffness per **ACI 318-19 Table 6.6.3.1.1(a)**.

## Usage

```python
config = {
    'stiffness_factors': {
        'beam':   0.35,
        'column': 0.70,
        'brace':  0.50,  # no ACI guidance — conservative
        'wall':   0.35,  # use 0.70 for uncracked
        'slab':   0.25,
    },
}
```

Set to ``None`` (default) or ``{}`` for gross (uncracked) stiffness.

## How it works

1. **Classifies** every frame element as ``beam``, ``column``, or ``brace``
   by geometry (vertical span vs horizontal span).
2. **Classifies** every area element as ``slab`` or ``wall`` by Z-span.
3. **Creates separate OpenSees section definitions** for each
   ``(section_name, element_type)`` pair, with ``E_mod`` scaled by the
   factor.  Classification details are in the ``Preprocessor._classify_element_type()``
   and ``OpenSeesBuilder._classify_element_type()`` docstrings.

## Typical ACI 318-19 factors

| Type | Factor | Notes |
|------|--------|-------|
| Beams | 0.35 | Table 6.6.3.1.1(a) |
| Columns | 0.70 | Table 6.6.3.1.1(a) |
| Walls (cracked) | 0.35 | Some practitioners use 0.50 |
| Walls (uncracked) | 0.70 | Table 6.6.3.1.1(a) |
| Slabs (two-way) | 0.25 | Table 6.6.3.1.1(a) |

## Interaction with SAP2000 stiffness modifiers

SAP2000 section modifiers (I3Mod, I2Mod, AMod, JMod) stack
multiplicatively with the ACI factor:

$$EI_{\text{effective}} = E_{\text{gross}} \times \text{ACI factor} \times I_{\text{gross}} \times \text{I3Mod}$$

Material-type filtering applies only to ``Concrete`` materials — steel
and other types retain gross stiffness.

> **Note:** Scaling ``E_mod`` is a broad approximation, not a true ACI
> cracked-section implementation.  ACI-style behaviour requires
> component-specific section modifiers (reducing I3/I2 while retaining
> A and J).
