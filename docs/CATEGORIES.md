# Product categories

When you enrich a CSV, you must pick the category that best matches your products. The category selects which AI prompt template is used.

Pick the **closest match**. If unsure, test with **1 row** and compare output quality across two nearby categories.

| Category key | Label in UI | Typical products |
|--------------|-------------|------------------|
| `polymertubing` | Medical Polymer Tubing | Pebax, nylon, polymer single/multi-lumen tubing |
| `heatshrink` | Medical Heat Shrink Tubing | Heat-shrink tubing |
| `metaltubing` | Medical Metal Tubing | Stainless, nitinol, metal hypotube |
| `catheters` | Medical Catheters & Accessories | Catheter shafts, introducers, microcatheters |
| `catheter-accessories` | Medical Catheter | (Uses catheters prompt) Catheter-related accessories |
| `balloons` | Medical Balloons | Balloon catheters, inflatable components |
| `dilators` | Medical Dilators | Dilators |
| `guidwires` | Medical Guidewires & Accessories | Guidewires, cores, tips |
| `needles` | Medical Needles & Scalpels | Needles, scalpels |
| `mandrels` | Medical Mandrels | Mandrels |
| `markerbands` | Radiopaque Marker Bands | Marker bands |
| `connectors` | Medical Connectors | Luer, hubs, connectors |
| `medicalwire` | Medical Wire | Wire products |
| `braidwire` | Braid Wires | Braid wire |
| `pull-wire` | Pull Wires | Pull wires |
| `metal-pins` | Medical Metal Pins | Pins |
| `electrodes` | Medical Electrodes | Electrodes |
| `sensors` | Medical Sensors | Sensors |
| `gears` | Medical Gears | Gears |
| `packaging` | Medical Packaging | Bags, packaging |
| `mesh-membranes` | Medical Mesh & Membranes | Mesh, membranes |
| `raw-materials` | Raw Materials | Raw stock materials |

## Editing or adding categories

Categories are defined in:

- `backend/categories.yaml` — registry (key, label, prompt file path)
- `backend/prompts/<key>.yaml` — prompt templates (`title` and `description` sections)

Adding a new category requires both files and a restart of the Docker stack. For most users, use the built-in list above.

Prompt files are YAML with `title.system`, `title.user`, `description.system`, and `description.user` fields.
