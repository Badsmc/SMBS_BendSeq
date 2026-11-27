# FreeCAD Addon Index submission preparation

Updated 2026-11-27 against the FreeCAD Addon Academy publishing guide and Index qualities.

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

The repository is public and was created on 2026-10-12. The public release target is main (currently 0.1.0); develop is now the 0.2.0 development preview. The manifest declares FreeCAD 1.1.3+ and Python 3.11+. The 0.2.0 branch uses the `freecad/bendseq` namespace layout with thin legacy entrypoint wrappers; startup uses host-managed paths.

Run Addon preflight and review results. Syntax, import inventory, manifest and headless tests do not prove CAD/GUI compatibility. Test a clean custom-repository installation with and without SheetMetal (DXF), TechDraw export, simulation, tooling, FAST, Mesh and document reopen. Verify other workbenches' UI is unaffected.

## Licensing status

- Code and original assets are licensed LGPL-2.1-or-later and CC-BY-SA-4.0; full texts and manifest SPDX identifiers are present. On 2026-11-27 maintainer Badsmc confirmed authorship of all 20 remaining CAD models and accompanying fixture settings; the [asset provenance](ASSET_PROVENANCE.md) inventory records their distribution under CC-BY-SA-4.0.

## Remaining release gates

- Confirm maintainer contact and repository topics freecad and addon.
- Demonstration screenshots and animations are included in the README. Record user testing feedback, FreeCAD versions, platforms and outcomes.
- Complete real FreeCAD acceptance; no claims of continuous collision certification.
- Submit Addon - Addition with repository URL and accurate notes. Publish the requested tag/release after review.

## Draft submission notes

SMBS BendSeq (PressBrakeSim) is an independent FreeCAD workbench for sheet-metal press-brake sequence planning, manual operations, FAST candidates, Coin3D previews and sampled collision checks. The public branch is `main` (0.1.0); the development preview is `develop` (0.2.0). Select and test the release candidate before submitting. The native runtime uses Part and FreeCAD-provided Qt/Pivy components. SheetMetal Workbench and TechDraw are optional depending on workflow. It is intended for preliminary part assessment. Calculated bend sequences and collision results may be incorrect or incomplete and require independent verification before machine use. It does not certify manufacturing feasibility. Authorship of the remaining bundled CAD models and fixture settings is recorded in the asset provenance inventory. Real FreeCAD test evidence must still be supplied before submitting this draft.
