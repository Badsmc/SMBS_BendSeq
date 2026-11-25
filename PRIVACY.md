# Privacy

SMBS is designed to process CAD geometry and bend sequences locally. The audited Python runtime does not contain an intentional network client, telemetry service, or automatic package installer.

FCStd session data is saved in the user-selected FreeCAD document. Tooling settings and selected models are stored locally. If the user enables debugging, local logs and crash information may contain document names, paths, geometry, machine settings and diagnostics. Review and redact files before manually attaching them to public issues.

The addon does not automatically upload these files. FreeCAD and its Addon Manager have their own network and privacy behavior. Users may remove local logs manually. See SECURITY.md for reporting concerns.
