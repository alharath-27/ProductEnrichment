# Product Enrichment Workflow

This tool takes a WooCommerce product export and returns the same file with improved product titles and descriptions.

The goal is to make product pages cleaner and more consistent without changing inventory, pricing, images, categories, or attributes.

## Input

The input is a standard WooCommerce CSV export.

The main fields used are:

- `SKU`
- `Name`
- `Short description`
- `Description`
- WooCommerce product attributes

Attributes are the source of truth for product specs.

Example attributes:

```text
Gauge: 18
Wall Thickness: Thin Wall
Cannula Length: 7.0 cm
Material: Polycarbonate
Packaging: 25/bag
```

If the old product name conflicts with the attributes, the attributes win.

## Category Selection

The user selects a category before enrichment.

Examples:

- Needles & Scalpels
- Catheters
- Guidewires
- Balloons
- Polymer Tubing

Each category has its own title and description rules. These live in `backend/prompts/`.

This is important because different product types need different copy structures.

## What Happens During Enrichment

For each product row:

1. The app reads the product name, description, SKU, and attributes.
2. It builds or generates a category-specific title.
3. It generates a product description using the category rules.
4. It checks that specific specs come from the attributes.
5. It writes the enriched title and description back into the CSV.

## Titles

Titles should be consistent within a category.

For Needles & Scalpels, titles are built from attributes in a fixed format.

Example:

```text
Percutaneous Access Needle, 18 Gauge, Thin Wall, 7.0 cm Length, Polycarbonate, Pink, 25/Bag, by Chamfr
```

The title should be predictable:

- same structure
- same separators
- same ending
- no invented specs

## Descriptions

Descriptions are generated with AI from the category prompt.

They use:

- the product attributes
- the original product text as context
- the new title
- the selected category rules

Descriptions should be accurate, but they do not need to be identical in structure. Some variation is intentional so the catalog does not read like duplicated copy.

## QA Checks

The app checks for common problems:

- invented specs
- changed numbers
- unsupported materials
- cross-product mixing
- claims not supported by the product data

Example: if an attribute says `7.0 cm`, the output should not rewrite it as `7.5 cm`.

## Output

The output CSV keeps the original WooCommerce structure.

Only these copy fields are updated:

- `Name` or `Title`
- `Description`

Everything else is preserved.

That makes the file easier to import back into WooCommerce.

## Recommended Use

1. Run 1 product first.
2. Review the title and description.
3. Confirm the right category was selected.
4. Run a small batch.
5. Spot-check the result.
6. Run the full batch.
7. Import the enriched CSV into WooCommerce.

## Summary

The system uses WooCommerce attributes to keep specs grounded, category templates to keep titles consistent, and AI prompts to improve product descriptions.
