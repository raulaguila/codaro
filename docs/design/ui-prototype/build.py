"""Rebuild the single, offline Codaro design proposal and its review package."""

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent


def build_html():
    document = (ROOT / "template.html").read_text()
    for marker, filename in [("STYLE", "style.css"), ("CORE", "core.js"), ("UI", "ui.js")]:
        document = document.replace(f"/* {marker} */", (ROOT / filename).read_text())
    (ROOT / "prototype.html").write_text(document)


def export_catalog():
    source = """
const fs = require('fs'), vm = require('vm');
const scope = vm.createContext({});
vm.runInContext(fs.readFileSync('core.js', 'utf8'), scope);
const C = scope.CodaroPrototype;
process.stdout.write(JSON.stringify({screens:C.screens,commands:C.commands,
  models:C.models,modes:C.modes,questions:C.questions,
  plans:Object.fromEntries(C.screens.map(s => [s.id,C.executionPlan(C.initial(s.id))]))
}, null, 2) + '\\n');
"""
    result = subprocess.run(
        ["node", "-e", source], cwd=ROOT, capture_output=True, text=True, check=True
    )
    (ROOT / "screens.json").write_text(result.stdout)
    return json.loads(result.stdout)


def make_overview():
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)

    def tag(name):
        return f"{{{ns}}}{name}"

    canvas = ET.Element(tag("svg"), {"width": "1720", "height": "1950", "viewBox": "0 0 1720 1950"})
    ET.SubElement(canvas, tag("rect"), {"width": "1720", "height": "1950", "fill": "#0b1017"})

    def label(x, y, value, size=18, color="#83d6d7"):
        element = ET.SubElement(
            canvas,
            tag("text"),
            {
                "x": str(x),
                "y": str(y),
                "fill": color,
                "font-family": "DejaVu Sans,sans-serif",
                "font-size": str(size),
            },
        )
        element.text = value

    label(36, 43, "Codaro · proposta consolidada de UI/UX", 25, "#dfe6ef")
    label(
        36,
        72,
        "Conversa como foco · perguntas integradas ao input · detalhes sob demanda",
        15,
        "#94a3b6",
    )
    scenes = [
        ("welcome", "Início limpo"),
        ("plan-chat", "Chat com plano fixo junto ao input"),
        ("ask-choice", "Pergunta e resposta no mesmo componente"),
        ("mode", "Modo de trabalho"),
        ("provider-custom", "Configuração do provedor"),
        ("models", "Seleção de modelo"),
    ]
    for index, (scene, title) in enumerate(scenes):
        x, y = 36 + index % 2 * 840, 119 + index // 2 * 607
        label(x, y, f"{index + 1:02} · {title}")
        frame = ET.SubElement(
            canvas,
            tag("svg"),
            {
                "x": str(x),
                "y": str(y + 17),
                "width": "808",
                "height": "548",
                "viewBox": "0 0 1120 760",
            },
        )
        frame.append(
            copy.deepcopy(ET.parse(ROOT / "screens" / f"{scene}.svg").getroot().find(tag("g")))
        )
    label(
        36,
        1930,
        "Maquetes ilustrativas · proposta única para adoção · dados fictícios",
        14,
        "#94a3b6",
    )
    overview = ROOT / "overview.svg"
    ET.ElementTree(canvas).write(overview, encoding="unicode")
    with tempfile.TemporaryFile() as log:
        subprocess.run(
            [
                "inkscape",
                str(overview),
                "--export-type=png",
                "--export-filename=" + str(ROOT / "overview.png"),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )


def validate_links(data):
    for scene in data["screens"]:
        ET.parse(ROOT / "screens" / f"{scene['id']}.svg")
    document = (ROOT / "prototype.html").read_text()
    assert all(f"/* {marker} */" not in document for marker in ["CORE", "UI", "STYLE"])
    assert 'id="draft"' in document
    assert not (ROOT / "render.log").exists()
    css = (ROOT / "style.css").read_text()
    colors = {
        key: re.search(rf"--{key}:\s*(#[0-9a-fA-F]{{6}})", css)[1]
        for key in ["bg", "panel", "raised", "text", "muted", "faint"]
    }

    def luminance(value):
        channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(channels, [0.2126, 0.7152, 0.0722], strict=True))

    for foreground in ["text", "muted", "faint"]:
        for background in ["bg", "panel", "raised"]:
            ratio = (luminance(colors[foreground]) + 0.05) / (luminance(colors[background]) + 0.05)
            assert ratio >= 4.5, (foreground, background, ratio)


def package():
    archive = ROOT / "codaro-ui-prototype.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for file in sorted(ROOT.rglob("*")):
            if (
                file.is_file()
                and file != archive
                and file.suffix != ".log"
                and "__pycache__" not in file.parts
            ):
                bundle.write(file, "codaro-ui-prototype/" + str(file.relative_to(ROOT)))
    with ZipFile(archive) as bundle:
        assert bundle.testzip() is None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html-only", action="store_true", help="Rebuild only the standalone HTML")
    args = parser.parse_args()
    build_html()
    if args.html_only:
        print(ROOT / "prototype.html")
        return
    if not shutil.which("node") or not shutil.which("inkscape"):
        raise SystemExit(
            "Full rebuild requires Node.js and Inkscape. Use --html-only to rebuild only the HTML."
        )
    data = export_catalog()
    result = subprocess.run(
        ["node", "validate.mjs"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    (ROOT / "validation.txt").write_text(result.stdout)
    subprocess.run([sys.executable, str(ROOT / "render_screens.py")], cwd=ROOT, check=True)
    make_overview()
    validate_links(data)
    package()
    print(result.stdout.splitlines()[-1])
    print(
        "Proposta consolidada: prototype.html, gallery.html, overview.png e codaro-ui-prototype.zip"
    )


if __name__ == "__main__":
    main()
