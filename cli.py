"""Command line for layer 1.

The commands exist to make the layer auditable by hand: ingest something, read
what happened to every document, see what could not be read and why, and reopen
any citation in the original bytes.

``show`` is the one that matters. It reads the unit out of the database and
then slices the fragment out of the blob independently, so a citation is
something you check rather than something you trust.
"""

from __future__ import annotations

import argparse
import sys

from core.blobs import BlobNotFound
from core.provenance import ProvenanceError
from core.workspace import Workspace
from ingest.connectors import ConnectorError
from ingest.extractors import availability_report
from ingest.ledger import Ledger
from ingest.pipeline import ingest
from ingest.triage import Relevance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="noc-brain", description="Cerebro operacional para el NOC — capa 1: ingesta"
    )
    parser.add_argument("--root", help="carpeta de trabajo (por defecto, la actual)")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("ingest", help="incorporar una fuente")
    run.add_argument("target", help="repo git, carpeta, archivo o texto")
    run.add_argument("--kind", help="forzar conector: filesystem, git, file, text")

    commands.add_parser("runs", help="listar corridas")

    events = commands.add_parser("ledger", help="ver el libro de una corrida")
    events.add_argument("--run", help="id de corrida (por defecto, la última)")
    events.add_argument("--outcome", help="extracted, unchanged, skipped o failed")
    events.add_argument("--limit", type=int, default=50)

    gaps = commands.add_parser("gaps", help="qué no se pudo leer y por qué")
    gaps.add_argument("--run", help="id de corrida (por defecto, todas)")

    show = commands.add_parser("show", help="reabrir una unidad en la fuente")
    show.add_argument("unit_id")

    find = commands.add_parser("find", help="buscar texto literal entre las unidades")
    find.add_argument("needle")
    find.add_argument("--limit", type=int, default=10)

    commands.add_parser("doctor", help="qué formatos se pueden leer hoy")
    commands.add_parser("stats", help="cuánto hay guardado")

    args = parser.parse_args(argv)
    workspace = Workspace.resolve(args.root)

    handlers = {
        "ingest": _ingest,
        "runs": _runs,
        "ledger": _ledger,
        "gaps": _gaps,
        "show": _show,
        "find": _find,
        "doctor": _doctor,
        "stats": _stats,
    }
    try:
        return handlers[args.command](args, workspace)
    except (ConnectorError, ProvenanceError, BlobNotFound) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _ingest(args, workspace: Workspace) -> int:
    summary = ingest(args.target, workspace, kind=args.kind)
    print(summary.line())
    store = workspace.store()
    try:
        gaps = Ledger(store).gaps(summary.run_id)
    finally:
        store.close()
    if gaps:
        print("\nHuecos de esta corrida:")
        for gap in gaps:
            print(f"  {gap.line()}")
    return 0


def _runs(args, workspace: Workspace) -> int:
    store = workspace.store()
    try:
        runs = Ledger(store).runs()
        if not runs:
            print("Todavía no hay corridas.")
            return 0
        for summary in runs:
            print(f"{summary.started_utc}  {summary.status:<8} {summary.line()}")
    finally:
        store.close()
    return 0


def _ledger(args, workspace: Workspace) -> int:
    store = workspace.store()
    try:
        ledger = Ledger(store)
        run_id = args.run
        if not run_id:
            last = ledger.last_run()
            if last is None:
                print("Todavía no hay corridas.")
                return 0
            run_id = last.run_id
        events = ledger.events(run_id=run_id, outcome=args.outcome, limit=args.limit)
        print(f"Corrida {run_id}: {len(events)} evento(s)")
        for event in events:
            suffix = f" — {event['reason']}" if event["reason"] else ""
            detail = f" ({event['detail']})" if event["detail"] else ""
            units = f" [{event['units']} unidades]" if event["units"] else ""
            print(f"  {event['outcome']:<10} {event['path']}{units}{suffix}{detail}")
    finally:
        store.close()
    return 0


def _gaps(args, workspace: Workspace) -> int:
    store = workspace.store()
    try:
        gaps = Ledger(store).gaps(args.run)
    finally:
        store.close()
    if not gaps:
        print("Sin huecos registrados.")
        return 0
    print("Documentos que no se volvieron legibles:")
    for gap in gaps:
        print(f"  {gap.line()}")
    return 0


def _show(args, workspace: Workspace) -> int:
    store = workspace.store()
    try:
        unit = store.get_unit(args.unit_id)
        if unit is None:
            print(f"No existe la unidad {args.unit_id}", file=sys.stderr)
            return 1
        print(f"Cita:  {unit.evidence.cite()}")
        print(f"Blob:  {unit.span.blob_sha}")
        print(f"Lector: {unit.extractor} | relevancia {Relevance(unit.relevance).name.lower()}")
        if unit.sensitivity != "none":
            print(f"Sensibilidad: {unit.sensitivity} ({', '.join(unit.sensitivity_labels)})")
        print("\n--- texto guardado ---")
        print(unit.text)

        resolution = workspace.blobs().resolve(unit.span, stored_text=unit.text)
        print("\n--- releído desde el blob original ---")
        print(resolution.text)
        if not resolution.exact:
            print(f"\n{resolution.note}")
        elif unit.verbatim:
            verdict = "coincide" if resolution.text == unit.text else "NO COINCIDE"
            print(f"\nVerificación byte a byte: {verdict}")
        else:
            print(
                f"\nEl lector {unit.extractor} normalizó el texto, así que lo guardado "
                "es una versión legible de estas líneas, no su copia literal."
            )
    finally:
        store.close()
    return 0


def _find(args, workspace: Workspace) -> int:
    store = workspace.store()
    try:
        units = store.find_units(args.needle, limit=args.limit)
        if not units:
            print("Sin coincidencias.")
            return 0
        for unit in units:
            head = unit.text.strip().splitlines()[0][:90]
            print(f"{unit.id}  {unit.evidence.cite()}\n    {head}")
    finally:
        store.close()
    return 0


def _doctor(args, workspace: Workspace) -> int:
    print("Formatos que este equipo puede leer hoy:\n")
    for item in availability_report():
        mark = "si " if item.available else "NO "
        formats = ", ".join(item.formats[:6]) or "catch-all"
        print(f"  [{mark}] {item.name:<12} {formats}")
        if not item.available:
            print(f"        falta {item.requirement} — {item.hint}")
    missing = [item for item in availability_report() if not item.available]
    if missing:
        print(
            f"\n{len(missing)} formato(s) se guardan pero no se leen. "
            "Los documentos afectados quedan registrados como huecos."
        )
    return 0


def _stats(args, workspace: Workspace) -> int:
    store = workspace.store()
    try:
        for table, count in store.counts().items():
            print(f"  {count:>8}  {table}")
        sensitive = store.sensitive_units(limit=1000)
        if sensitive:
            print(f"\n  {len(sensitive):>8}  unidades marcadas como sensibles")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
