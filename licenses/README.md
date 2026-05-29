# Full License Texts

This folder holds the complete, verbatim license texts for the third-party components listed in `../THIRD_PARTY_NOTICES.md`. Most permissive licenses (MIT, BSD, Apache-2.0) require that the original copyright notice and license text be retained when you use the software — this folder is where those texts live so that requirement is met.

## How to add one

1. Find the dependency's `LICENSE` file (in its source repo or its installed package metadata).
2. Save the verbatim text here as `<tool-name>-LICENSE.txt` (e.g. `flask-LICENSE.txt`, `pandas-LICENSE.txt`).
3. For Apache-2.0 projects that ship a `NOTICE` file, include that too as `<tool-name>-NOTICE.txt`.

A practical shortcut for Python dependencies: the installed package usually carries its license in its `*.dist-info/` metadata, so the texts can be collected from the environment rather than hunted down one by one.

> Tip: the same license text is shared by many dependencies (e.g. one MIT text covers dozens of them). You can keep one canonical copy per license family (`MIT.txt`, `Apache-2.0.txt`, `BSD-3-Clause.txt`) and note in `THIRD_PARTY_NOTICES.md` which one applies, as long as you also keep any project-specific copyright lines and `NOTICE` files.
