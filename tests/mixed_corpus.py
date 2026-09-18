"""Builds the deliberately ugly folder the acceptance test runs against.

It is generated rather than committed so the repository holds no binary blobs,
and so the PDFs can be built two ways: one with a text layer and one without,
which is the difference between a document we can read and a scan we cannot.
"""

from __future__ import annotations

import binascii
import struct
import zlib
from pathlib import Path

CONNECTION_STRING = "postgres://wallet:s3cretpass@db:5432/wallet"


def build(root: Path) -> Path:
    """Create the corpus under ``root`` and return it."""
    root = Path(root)
    _python_repo(root / "repo-python")
    _typescript_repo(root / "repo-typescript")
    _documents(root / "docs")
    _leftovers(root)
    return root


def _python_repo(root: Path) -> None:
    (root / "services").mkdir(parents=True)
    _write(
        root / "README.md",
        "# wallet-core\n\n"
        "Billetera digital. Corre en AR, MX, CO, PE y CL.\n\n"
        "## Dependencias\n\n"
        "Depende de ledger y del proveedor de SMS.\n",
    )
    _write(
        root / "services" / "wallet.py",
        "import requests\n\n\n"
        "def cobrar(monto):\n"
        '    """Debita en ledger."""\n'
        "    return requests.post('http://ledger/debitar', json={'monto': monto})\n",
    )
    _write(root / "requirements.txt", "requests==2.31.0\n")
    _write(
        root / "docker-compose.yml",
        "services:\n"
        "  wallet:\n"
        "    image: wallet:1.4.2\n"
        "    depends_on:\n"
        "      - ledger\n"
        "  ledger:\n"
        "    image: ledger:3.0.1\n",
    )
    _write(root / ".env", f"DATABASE_URL={CONNECTION_STRING}\n")


def _typescript_repo(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    _write(
        root / "src" / "index.ts",
        "import { Queue } from './queue';\n\n"
        "export function procesar(evento: string): void {\n"
        "  Queue.publish('wallet.eventos', evento);\n"
        "}\n",
    )
    _write(
        root / "package.json",
        '{\n  "name": "wallet-web",\n  "version": "1.0.0",\n'
        '  "dependencies": {\n    "left-pad": "^1.3.0"\n  }\n}\n',
    )
    _write(root / "package-lock.json", '{"lockfileVersion": 3}\n')
    _write(root / "node_modules" / "left-pad" / "index.js", "module.exports = 1;\n")


def _documents(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "runbook.pdf").write_bytes(
        pdf_with_text("Runbook wallet: reiniciar el consumidor de la cola de pagos.")
    )
    (root / "escaneado.pdf").write_bytes(pdf_without_text())
    (root / "captura.png").write_bytes(png_pixel())
    _write(root / "topologia.drawio", DRAWIO)
    _write(root / "confluence-export.html", CONFLUENCE)
    _write(
        root / "incidentes.csv",
        "fecha,servicio,pais,alerta\n"
        "2026-08-01,wallet,AR,latencia\n"
        "2026-08-03,ledger,MX,timeout\n",
    )
    _write(root / "flujo.mmd", "flowchart LR\n  wallet --> ledger\n  wallet --> sms\n")


def _leftovers(root: Path) -> None:
    (root / "vendor").mkdir(parents=True)
    _write(root / "vendor" / "jquery.min.js", "!function(e){}(window);\n")
    _write(root / "vacio.txt", "")
    _write(
        root / "notas.ipynb",
        '{"cells": [{"cell_type": "markdown", "source": ["# Analisis de alertas"]}]}',
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


# -- synthetic binaries --------------------------------------------------


def pdf_with_text(message: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({message}) Tj ET".encode("latin-1")
    return _pdf(stream, fonts=True)


def pdf_without_text() -> bytes:
    """A page that only draws a rectangle: the shape of a scan, for our purposes."""
    return _pdf(b"0.2 0.4 0.9 rg 100 100 300 200 re f", fonts=False)


def _pdf(content: bytes, fonts: bool) -> bytes:
    resources = "<</Font<</F1 4 0 R>>>>" if fonts else "<<>>"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            f"/Resources{resources}/Contents 5 0 R>>"
        ).encode("ascii"),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(content), content),
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % number + body + b"endobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


def png_pixel() -> bytes:
    """A valid 1x1 PNG. Enough for routing; OCR will find nothing in it."""
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\xff\xff")
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        _chunk(kind, payload)
        for kind, payload in ((b"IHDR", header), (b"IDAT", pixels), (b"IEND", b""))
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body))


DRAWIO = """<mxfile host="app.diagrams.net">
  <diagram name="Topologia">
    <mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="wallet" value="wallet-core" vertex="1"/>
        <mxCell id="ledger" value="ledger" vertex="1"/>
        <mxCell id="sms" value="Proveedor SMS" vertex="1"/>
        <mxCell id="e1" value="debita" edge="1" source="wallet" target="ledger"/>
        <mxCell id="e2" value="notifica" edge="1" source="wallet" target="sms"/>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""

CONFLUENCE = """<html>
<head><title>Monitoreo de wallet</title></head>
<body>
  <h1>Monitoreo de wallet</h1>
  <p>El servicio wallet emite logs en formato JSON hacia stdout.</p>
  <p>Hoy monitoreamos solamente la latencia del endpoint de cobro.</p>
  <script>var analytics = 1;</script>
</body>
</html>
"""
