# FreeCAD Addon Index submission preparation

Reviewed 2026-11-25 against the FreeCAD Addon Academy publishing guide and Index qualities.

- https://freecad.github.io/Addon-Academy/Guides/Publishing/Indexed
- https://freecad.github.io/Addon-Academy/Topics/Addon-Index/Index/Qualities
- https://freecad.github.io/Addon-Academy/Topics/Structuring/Manifest/
- https://freecad.github.io/Addon-Academy/Topics/Dependencies/Allow-List/
- Submission: https://github.com/FreeCAD/Addons/issues/new?template=Addon-Addition.yml

## Runtime dependencies

| Component | Declaration | Purpose |
| --- | --- | --- |
| Part | required internal | Native geometry |
| SheetMetal Workbench | optional addon | SheetMetal unfold; ASCII DXF does not require it |
| TechDraw | optional internal | Drawing export |
| PySide and Pivy/Coin3D | host components | FreeCAD Qt and native viewer; no pip installation |
| PythonOCC | not a runtime requirement | Legacy optional offline reader |
| jsonschema | not required | Tooling-set v1 validation uses Python standard library |

No external Python package is needed for the supported native runtime. Do not add legacy headless dependencies or install packages from the addon. SheetMetal manages its own dependencies.

## Preflight

The repository is public and was created on 2026-10-12. The release target is main, version 0.1.0; develop is for active development. The manifest declares FreeCAD 1.1.0+ and Python 3.11+. The existing legacy layout is retained to avoid a large untested migration; startup uses host-managed paths. Namespace migration is recommended for future work.

Run Addon preflight and review results. Syntax, import inventory, manifest and headless tests do not prove CAD/GUI compatibility. Test a clean custom-repository installation with and without SheetMetal (DXF), TechDraw export, simulation, tooling, FAST, Mesh and document reopen. Verify other workbenches' UI is unaffected.

## Remaining release gates

- Code and original assets are licensed LGPL-2.1-or-later and CC-BY-SA-4.0; full texts and manifest SPDX identifiers are present. Confirm redistribution rights for the supplied CAD files and resolve [asset provenance](ASSET_PROVENANCE.md) before submission.
- Confirm maintainer contact and repository topics freecad and addon.
- Capture authentic screenshots and user testing feedback; record FreeCAD versions, platforms and outcomes.
- Complete real FreeCAD acceptance; no claims of continuous collision certification.
- Submit Addon - Addition with repository URL and accurate notes. Publish the requested tag/release after review.

## Draft submission notes

SMBS BendSeq (PressBrakeSim) 0.1.0 is an independent FreeCAD workbench for sheet-metal press-brake sequence planning, manual operations, FAST candidates, Coin3D previews and sampled collision checks. The public branch is main. The native runtime uses Part and FreeCAD-provided Qt/Pivy components. SheetMetal Workbench and TechDraw are optional depending on workflow. It does not certify manufacturing feasibility. CAD redistribution permissions and real FreeCAD test evidence must be supplied before submitting this draft.
