# Product Enrichment Workflow

This document explains what happens when we enrich products from a WooCommerce export.

The goal is simple:

```text
WooCommerce CSV export -> improved product titles and descriptions -> import-ready WooCommerce CSV
```

---

## 1. Input

We start with a standard WooCommerce product export CSV.

Important fields include:

- `SKU`
- `Name`
- `Short description`
- `Description`
- WooCommerce attribute columns, such as `Attribute 1 name`, `Attribute 1 value(s)`, etc.

The attributes are the most important part because they contain the actual product specifications.

Examples:

```text
Gauge = 18
Wall Thickness = Thin Wall
Cannula Length = 7.0 cm
Material = Polycarbonate
Packaging = 25/bag
```

---

## 2. Category Selection

The user selects a product category in the app.

Examples:

- Needles & Scalpels
- Catheters
- Guidewires
- Balloons
- Polymer Tubing

Each category has its own YAML prompt file under `backend/prompts/`.

Those YAML files define:

- the expected title template
- title rules
- description rules
- category-specific writing style

This matters because a catheter title should not be structured the same way as a scalpel title.

---

## 3. Attribute Extraction

For each product row, the backend reads all WooCommerce attribute pairs:

```text
Attribute 1 name       -> Attribute 1 value(s)
Attribute 2 name       -> Attribute 2 value(s)
...
```

It converts them into a structured dictionary.

Example:

```json
{
  "Needles & Scalpels Type": "Percutaneous Access Needle",
  "Gauge": "18",
  "Wall Thickness": "Thin Wall",
  "Cannula Length": "7.0 cm",
  "Material": "Polycarbonate",
  "Color": "Pink",
  "Packaging": "25/bag"
}
```

These attributes are treated as the source of truth.

If the original product name says one thing but the attributes say another, the enrichment process trusts the attributes.

---

## 4. Title Enrichment

The title is built to be consistent across products in the same category.

For some categories, the AI uses the category's YAML title template and then validates the output.

For Needles & Scalpels, titles are built deterministically from attributes to keep the structure consistent.

Example output:

```text
Percutaneous Access Needle, 18 Gauge, Thin Wall, 7.0 cm Length, Polycarbonate, Pink, 25/Bag, by Chamfr
```

The title process focuses on:

- consistent structure
- correct product type
- exact dimensions and specs
- no invented values
- same separator style
- same ending pattern

Descriptions are allowed to vary, but titles should stay template-driven.

---

## 5. Description Enrichment

Descriptions are generated with AI using the category-specific YAML prompt.

The description uses:

- product attributes
- original name and description for context
- the newly generated title
- category writing rules

The descriptions are intentionally less rigid than titles. They should sound tailored to each product instead of repeating the exact same sentence pattern.

The AI is instructed to:

- describe the product clearly
- include important technical specs
- avoid unsupported claims
- avoid regulatory claims unless present in the source data
- vary sentence structure between products

---

## 6. QA and Validation

After title and description generation, the system checks the output.

The key rule is:

```text
Specific specs must come from the product attributes.
```

The validation checks for things like:

- invented dimensions
- rounded numbers
- unsupported material names
- cross-product mixing
- specs that do not exist in the attribute data

Example:

If the attributes say:

```text
Cannula Length = 7.0 cm
```

The output should not change that to:

```text
7 cm
```

or:

```text
7.5 cm
```

The system tries to preserve exact attribute values.

---

## 7. Output

The final output is a WooCommerce-ready CSV.

It keeps the original input file structure and updates only the enriched copy fields:

- `Name` or `Title` becomes the enriched title
- `Description` becomes the enriched description
- all other columns are preserved

This makes the output easier to import back into WooCommerce.

---

## 8. Human Review

The recommended workflow is:

1. Run 1 product first as a test.
2. Review the title and description.
3. Confirm the selected category is correct.
4. Run a small batch.
5. Spot-check the output.
6. Run the full batch.
7. Import the enriched CSV into WooCommerce.

---

## Summary

The enrichment workflow improves product copy while keeping product specs grounded in WooCommerce attributes.

Titles are designed to be consistent and template-based.

Descriptions are designed to be richer and more varied.

The final output stays import-ready for WooCommerce.
