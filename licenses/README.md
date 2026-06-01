# Full License Texts

This folder holds the complete, verbatim license texts for the third-party components listed in `../THIRD_PARTY_NOTICES.md`. Most permissive licenses (MIT, BSD, Apache-2.0) require that the original copyright notice and license text be retained when you use the software — this folder is where those texts live so that requirement is met.

## How to add one

1. Find the dependency's `LICENSE` file (in its source repo or its installed package metadata).
2. Save the verbatim text here as `<tool-name>-LICENSE.txt` (e.g. `flask-LICENSE.txt`, `pandas-LICENSE.txt`).
3. For Apache-2.0 projects that ship a `NOTICE` file, include that too as `<tool-name>-NOTICE.txt`.

A practical shortcut for Python dependencies: the installed package usually carries its license in its `*.dist-info/` metadata, so the texts can be collected from the environment rather than hunted down one by one.

> Tip: the same license text is shared by many dependencies (e.g. one MIT text covers dozens of them). You can keep one canonical copy per license family (`MIT.txt`, `Apache-2.0.txt`, `BSD-3-Clause.txt`) and note in `THIRD_PARTY_NOTICES.md` which one applies, as long as you also keep any project-specific copyright lines and `NOTICE` files.

## Current layout

This folder is populated using that canonical-family approach:

- **License-family bodies** — `MIT.txt`, `BSD-3-Clause.txt`, `Apache-2.0.txt`, `PostgreSQL.txt`, `LGPL-2.1.txt`, `LGPL-3.0.txt`, `GPL-2.0.txt`, `BUSL-1.1.txt`. One verbatim copy each; shared by every dependency under that license.
- **`COPYRIGHT-NOTICES.txt`** — the per-project copyright line for every component in `../THIRD_PARTY_NOTICES.md`, grouped by family. This is what makes the shared MIT/BSD bodies compliant (those licenses embed a per-project holder).
- **`<project>-NOTICE.txt`** — verbatim `NOTICE` files for the Apache-2.0 projects that ship one (`airflow`, `kafka`, `requests`, `snowflake-connector-python`), retained per Apache-2.0 §4(d).
- **`beautifulsoup4-LICENSE.txt`** — kept as a standalone file (it bundles extra html5lib/soupsieve notices).

When adding a dependency: if its license family is already here, just add its copyright line to `COPYRIGHT-NOTICES.txt` (and its `NOTICE` file if any). If it introduces a new family, add that family's verbatim text as `<SPDX-id>.txt`.
