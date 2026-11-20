"""Collect the objects created by SMBS in the FreeCAD model tree."""

GROUP_NAME = "SMBS_Objects"


def group_document_objects(document):
    """Group SMBS objects by internal name, including exports from older versions."""
    owned = []
    for name, type_id in (
        ("SMBS_Data", "App::FeaturePython"),
        ("SMBS_BendSchedule", "Spreadsheet::Sheet"),
        ("SMBS_BendDrawing", "Part::Feature"),
        ("DXFUnfold", "App::DocumentObjectGroup"),
    ):
        obj = document.getObject(name)
        if obj is not None and obj.TypeId == type_id:
            owned.append(obj)

    # FoldedPart is a generic name used by the DXF importer. Only collect it
    # when the document also contains the importer's original DXF contour.
    if any(obj.Name == "DXFUnfold" and
           any(child.Name == "DXFContours" for child in obj.Group) for obj in owned):
        folded = document.getObject("FoldedPart")
        if folded is not None and folded.TypeId == "Part::Feature":
            owned.append(folded)

    if not owned:
        return
    group = document.getObject(GROUP_NAME)
    if group is None:
        group = document.addObject("App::DocumentObjectGroup", GROUP_NAME)
        group.Label = "SMBS"
    elif group.TypeId != "App::DocumentObjectGroup":
        raise ValueError(f"{GROUP_NAME} уже используется объектом другого типа.")
    for obj in owned:
        if obj not in group.Group:
            group.addObject(obj)
