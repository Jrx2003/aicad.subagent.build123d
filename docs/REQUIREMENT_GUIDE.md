# Requirement Guide (for stable iterative convergence)

Use these patterns when preparing requirement text/payload:

## Good examples

1. `Create a 60x40x8 mm plate. Add four through holes of diameter 6 mm at corners with 8 mm margin. Add 1.5 mm fillet on all outer top edges.`
2. `Build a 30x30x20 mm block with a centered blind hole diameter 10 mm depth 12 mm. Chamfer top outer edges by 1 mm.`

## Less stable patterns

1. Vague size language (`small`, `thin`, `a bit larger`).
2. Missing feature quantity/position (`add holes` without count/location).
3. Conflicting constraints (`very thin` and `very strong` without dimensions/material assumptions).

## Suggested JSON structure

```json
{
  "description": "Create a 60x40x8 mm plate with four corner holes and top-edge fillets.",
  "dimensions": {
    "length": 60,
    "width": 40,
    "thickness": 8
  },
  "features": ["hole", "fillet"],
  "constraints": ["all units in mm", "through holes"]
}
```
