# SMBS BendSeq

SMBS BendSeq is a FreeCAD workbench for planning press-brake operations, preparing bend sequences, and previewing tooling and sheet geometry.

## Repository branches

| Branch | Role | Current status |
|---|---|---|
| [main](https://github.com/Badsmc/SMBS_BendSeq/tree/main) | Stable public release destination | Historical baseline; release integration and acceptance pending |
| [develop](https://github.com/Badsmc/SMBS_BendSeq/tree/develop) | Active addon development | Latest installable development preview; not qualified for production |

The development branch was previously named `smbs`. Its commit history has been preserved.

For the current development version and installation instructions, see the [develop README](https://github.com/Badsmc/SMBS_BendSeq/blob/develop/README.md) or download the [development ZIP](https://github.com/Badsmc/SMBS_BendSeq/archive/refs/heads/develop.zip). Install only one checkout in FreeCAD's Mod directory at a time.

Historical implementations are retained as [archive tags](https://github.com/Badsmc/SMBS_BendSeq/tags) under `archive/2026-11-25/`. The former 2D, 3D, GUI and core branches are no longer active development targets.

## Development and release policy

New features, fixes and experiments start from `develop`. Reviewed changes return to `develop` before release acceptance.

The current `main` still contains legacy code and reference data. It is not the latest addon and must not be treated as an already validated stable release. Reconcile its useful reference assets with the current development tree before promoting a release. See [BRANCH_ARCHITECTURE.md](BRANCH_ARCHITECTURE.md) for the acceptance gates.

A planned sequence or sampled collision check does not establish production readiness.
