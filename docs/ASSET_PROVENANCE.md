# CAD asset provenance and redistribution review

This inventory records an unresolved release gate; it does not assert ownership or permission from an external supplier.

| Location | Contents | Review status |
| --- | --- | --- |
| toollibrary/punch/ and toollibrary/die/ | Empty distributed directories; local user-created/imported tools only | No bundled punch/die files; user is responsible for permissions for their own tools |
| toollibrary/Backgauge/, Clamp/, machine_frame/, presses/ | Machine and fixture models | Confirm original authorship or permission |
| demo/parts/ | Sheet-metal example geometry | Confirm ownership and that no employer/customer restrictions apply |
| demo/legacy-tooling/ | Remaining fixture metadata; historical punch/die models and sets removed | Confirm provenance of remaining fixtures |
| Historical diagnostic scenes and bend-sequence JSON | Removed from published branch and tag histories | Not distributed |
| freecad/bendseq/resources/ | Project artwork and templates | Original project assets licensed CC-BY-SA-4.0; any separately sourced asset retains its terms |

For any externally sourced CAD file, record the source URL or supplier, author, applicable license or permission, and attribution. If permission cannot be established, replace the file with an original synthetic example before official Addon Index distribution. Punch and die CAD files and sidecars have been removed from the current distribution. The published branches and tags were rewritten with git-filter-repo to remove the audited tooling files, diagnostic scenes, historical tooling-data reports and duplicate blobs. Old commit objects, pull-request references, third-party copies and GitHub caches may still retain previous content. History rewriting is not a guarantee of complete erasure or legal clearance.

The code license is not a certification of machine geometry, collision clearance or safe manufacturing.
