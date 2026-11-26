"""Generate Qt Linguist catalogs from the existing BendSeq source keys.

Run python scripts/build_qt_translations.py --compile after updating a catalog.
Qt lrelease is a development tool only; the addon installs no dependencies.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1] / "freecad/bendseq" / "resources/translations"
# Qt Designer uses the form class as its translation context.
UI_RU = {
    "Mesh 3D": "Mesh 3D",
    "Mesh verification defaults": "\u041f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u044b \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 Mesh",
    "Time limit (s)": "\u041b\u0438\u043c\u0438\u0442 \u0432\u0440\u0435\u043c\u0435\u043d\u0438 (\u0441)",
    "Minimum penetration volume (mm\u00b3)": "\u041c\u0438\u043d\u0438\u043c\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u0431\u044a\u0451\u043c \u043f\u0440\u043e\u043d\u0438\u043a\u043d\u043e\u0432\u0435\u043d\u0438\u044f (\u043c\u043c\u00b3)",
}


def catalog(language):
    return json.loads((ROOT / language / "catalog.json").read_text(encoding="utf-8"))


def generate():
    english, russian = catalog("en"), catalog("ru")
    if english.keys() != russian.keys():
        raise ValueError("English and Russian catalog keys differ")
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<!DOCTYPE TS>',
             '<TS version="2.1" language="ru" sourcelanguage="en">',
             '  <context>', '    <name>BendSeq</name>']
    for key in sorted(english):
        lines.extend(['    <message>',
                      '      <source>' + escape(english[key]) + '</source>',
                      '      <comment>' + escape(key) + '</comment>',
                      '      <translation>' + escape(russian[key]) + '</translation>',
                      '    </message>'])
    lines.extend(['  </context>', '  <context>', '    <name>BendSeqMeshPreferences</name>'])
    for source, translated in UI_RU.items():
        lines.extend(['    <message>', '      <source>' + escape(source) + '</source>',
                      '      <translation>' + escape(translated) + '</translation>',
                      '    </message>'])
    lines.extend(['  </context>', '</TS>'])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--lrelease", help="Path to Qt 6 lrelease")
    args = parser.parse_args()
    ts = ROOT / "BendSeq_ru.ts"
    generated = generate()
    if args.check:
        if ts.read_text(encoding="utf-8") != generated:
            raise SystemExit("BendSeq_ru.ts is outdated; regenerate it")
    else:
        ts.write_text(generated, encoding="utf-8", newline="\n")
    if args.compile:
        executable = args.lrelease or next(
            (path for name in ("lrelease6", "lrelease-qt6", "lrelease")
             if (path := shutil.which(name))), None)
        if not executable:
            raise SystemExit("Qt 6 lrelease not found; provide --lrelease")
        subprocess.run([executable, str(ts), "-qm", str(ts.with_suffix(".qm"))], check=True)


if __name__ == "__main__":
    main()
