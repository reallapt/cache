import io
import csv
import json
import math
import mimetypes
import os
import posixpath
import shutil
import tempfile
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from flask import Flask, abort, jsonify, request, send_file, send_from_directory


app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024


@app.after_request
def disable_static_cache(response):
    if request.path == "/" or request.path.startswith("/app."):
        response.headers["Cache-Control"] = "no-store"
    return response

DATA_DIR = Path(os.getenv("DATA_DIR", "/data/files")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
LAYOUT_FILE = DATA_DIR / ".layout.json"
EXPIRY_FILE = DATA_DIR / ".expiry.json"
SETTINGS_FILE = DATA_DIR / ".settings.json"
EXPIRY_SECONDS = 24 * 60 * 60
EXPIRY_LOCK = threading.Lock()
TABLE_SUFFIXES = {".csv", ".tsv", ".xlsx"}


def safe_relative_path(raw_path: str) -> Path:
    path = PurePosixPath(raw_path.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        abort(400, "Invalid path")
    return Path(*path.parts)


def resolved_item(raw_path: str) -> Path:
    target = (DATA_DIR / safe_relative_path(raw_path)).resolve()
    if DATA_DIR not in target.parents:
        abort(400, "Invalid path")
    return target


def available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem} {index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def available_reserved_path(path: Path, reserved: set[Path]) -> Path:
    candidate = available_path(path)
    while candidate in reserved:
        stem, suffix = candidate.stem, candidate.suffix
        parts = stem.rsplit(" ", 1)
        index = int(parts[1]) + 1 if len(parts) == 2 and parts[1].isdigit() else 2
        base = parts[0] if len(parts) == 2 and parts[1].isdigit() else stem
        candidate = path.with_name(f"{base} {index}{suffix}")
    reserved.add(candidate)
    return candidate


def directory_size(path: Path) -> int:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def load_layout() -> dict:
    try:
        payload = json.loads(LAYOUT_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_layout(payload: dict) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=DATA_DIR, prefix=".layout-", delete=False) as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
        temporary = Path(file.name)
    os.replace(temporary, LAYOUT_FILE)


def load_expirations() -> dict[str, float]:
    try:
        payload = json.loads(EXPIRY_FILE.read_text(encoding="utf-8"))
        return {path: float(expires) for path, expires in payload.items()}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return {}


def save_expirations(payload: dict[str, float]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=DATA_DIR, prefix=".expiry-", delete=False) as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
        temporary = Path(file.name)
    os.replace(temporary, EXPIRY_FILE)


def load_settings() -> dict:
    try:
        payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_settings(payload: dict) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=DATA_DIR, prefix=".settings-", delete=False) as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
        temporary = Path(file.name)
    os.replace(temporary, SETTINGS_FILE)


def auto_delete_enabled() -> bool:
    return load_settings().get("autoDelete", True) is not False


def reset_expirations() -> None:
    with EXPIRY_LOCK:
        expires_at = time.time() + EXPIRY_SECONDS
        expirations = {
            path.name: expires_at
            for path in DATA_DIR.iterdir()
            if not path.name.startswith(".")
        }
        save_expirations(expirations)


def stored_target(raw_path: str) -> Path | None:
    path = PurePosixPath(raw_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    target = (DATA_DIR / Path(*path.parts)).resolve()
    return target if DATA_DIR in target.parents else None


def cleanup_expired() -> dict[str, float]:
    """Assign a 24-hour lifetime to untracked items and remove expired ones."""
    with EXPIRY_LOCK:
        now = time.time()
        expirations = load_expirations()
        should_delete = auto_delete_enabled()
        items = {path.name: path for path in DATA_DIR.iterdir() if not path.name.startswith(".")}
        changed = False
        removed = []

        for name, expires_at in list(expirations.items()):
            target = stored_target(name)
            if name not in items or target is None or not target.exists():
                expirations.pop(name, None)
                changed = True
            elif expires_at <= now and should_delete:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
                expirations.pop(name, None)
                removed.append(name)
                changed = True

        if removed:
            layout = load_layout()
            for name in removed:
                layout.pop(name, None)
            save_layout(layout)

        for name in items:
            if name not in expirations and (DATA_DIR / name).exists():
                expirations[name] = now + EXPIRY_SECONDS
                changed = True

        if changed:
            save_expirations(expirations)
        return expirations


def set_expiration(path: Path) -> float:
    with EXPIRY_LOCK:
        expirations = load_expirations()
        expires_at = time.time() + EXPIRY_SECONDS
        expirations[path.relative_to(DATA_DIR).as_posix()] = expires_at
        save_expirations(expirations)
        return expires_at


def remove_expiration(path: Path) -> None:
    with EXPIRY_LOCK:
        expirations = load_expirations()
        if expirations.pop(path.relative_to(DATA_DIR).as_posix(), None) is not None:
            save_expirations(expirations)


def item_payload(path: Path, layout: dict | None = None, expires_at: float | None = None) -> dict:
    stat = path.stat()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    suffix = path.suffix.lower()
    if path.is_dir():
        preview = "folder"
    elif suffix in TABLE_SUFFIXES:
        preview = "table"
    elif suffix in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"}:
        preview = "archive"
    elif suffix == ".pdf" or mime_type == "application/pdf":
        preview = "pdf"
    elif mime_type.startswith("image/"):
        preview = "image"
    elif mime_type.startswith("video/"):
        preview = "video"
    elif mime_type.startswith("audio/") or suffix in {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}:
        preview = "audio"
    elif mime_type.startswith("audio/") or suffix in {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}:
        preview = "audio"
    elif mime_type.startswith("text/") or suffix in {".md", ".markdown", ".json", ".log", ".csv", ".yaml", ".yml"}:
        preview = "text"
    else:
        preview = "file"
    return {
        "name": path.name,
        "path": path.relative_to(DATA_DIR).as_posix(),
        "kind": "folder" if path.is_dir() else "file",
        "mime": mime_type,
        "preview": preview,
        "size": directory_size(path) if path.is_dir() else stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "layout": (layout or {}).get(path.relative_to(DATA_DIR).as_posix(), {}),
        "expiresAt": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat() if expires_at else None,
    }


def normalize_table_rows(rows: list[list[object]], max_rows: int = 500, max_columns: int = 50) -> list[list[str]]:
    normalized = []
    for row in rows[:max_rows]:
        values = [str(value) if value is not None else "" for value in row[:max_columns]]
        while values and values[-1] == "":
            values.pop()
        if values:
            normalized.append(values)
    width = max((len(row) for row in normalized), default=0)
    return [row + [""] * (width - len(row)) for row in normalized]


def xlsx_sheet_path(workbook: zipfile.ZipFile) -> str:
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    sheet_path = "xl/worksheets/sheet1.xml"
    if "xl/workbook.xml" not in workbook.namelist() or "xl/_rels/workbook.xml.rels" not in workbook.namelist():
        return sheet_path
    workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
    first_sheet = workbook_root.find(f".//{{{main_ns}}}sheet")
    relationship_id = first_sheet.attrib.get(f"{{{rel_ns}}}id") if first_sheet is not None else None
    relations_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    for relation in relations_root.findall(f"{{{package_rel_ns}}}Relationship"):
        if relation.attrib.get("Id") == relationship_id:
            target = relation.attrib["Target"]
            return posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
    return sheet_path


def xlsx_table_rows(path: Path) -> list[list[str]]:
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path) as workbook:
        shared_strings = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            for item in shared_root.findall(f"{{{main_ns}}}si"):
                shared_strings.append("".join(node.text or "" for node in item.iter(f"{{{main_ns}}}t")))

        sheet_path = xlsx_sheet_path(workbook)
        root = ET.fromstring(workbook.read(sheet_path))
        row_values = {}
        max_column = 0
        for row_node in root.findall(f".//{{{main_ns}}}row"):
            values = {}
            for cell in row_node.findall(f"{{{main_ns}}}c"):
                reference = cell.attrib.get("r", "A1")
                column = 0
                for character in reference:
                    if not character.isalpha():
                        break
                    column = column * 26 + ord(character.upper()) - ord("A") + 1
                cell_type = cell.attrib.get("t")
                value_node = cell.find(f"{{{main_ns}}}v")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{{{main_ns}}}t"))
                elif value_node is None:
                    value = ""
                else:
                    value = value_node.text or ""
                    if cell_type == "s":
                        try:
                            value = shared_strings[int(value)]
                        except (ValueError, IndexError):
                            value = ""
                if column:
                    values[column - 1] = value
                    max_column = max(max_column, column)
            row_number = int(row_node.attrib.get("r", len(row_values) + 1))
            row_values[row_number] = values

        max_row = max(row_values, default=0)
        dimension = root.find(f"{{{main_ns}}}dimension")
        dimension_ref = dimension.attrib.get("ref", "") if dimension is not None else ""
        if ":" in dimension_ref:
            end_reference = dimension_ref.rsplit(":", 1)[1]
            end_column = "".join(character for character in end_reference if character.isalpha())
            end_row = "".join(character for character in end_reference if character.isdigit())
            if end_column:
                dimension_column = 0
                for character in end_column:
                    dimension_column = dimension_column * 26 + ord(character.upper()) - ord("A") + 1
                max_column = max(max_column, dimension_column)
            if end_row.isdigit():
                max_row = max(max_row, int(end_row))
        max_row = min(max_row, 500)
        max_column = min(max(max_column, 1), 50)
        rows = [[row_values.get(row_index, {}).get(column_index, "") for column_index in range(max_column)] for row_index in range(1, max_row + 1)]
        return rows


def xlsx_column_name(index: int) -> str:
    """Return the Excel column name for a zero-based column index."""
    name = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def save_xlsx_rows(path: Path, rows: list[list[object]]) -> None:
    """Update the first worksheet values while retaining the rest of the XLSX package."""
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    if not isinstance(rows, list) or not rows or not all(isinstance(row, list) for row in rows):
        abort(400, "Table is empty")
    rows = [[str(value) if value is not None else "" for value in row] for row in rows]
    if len(rows) > 500 or any(len(row) > 50 for row in rows):
        abort(413, "Table is too large")

    with zipfile.ZipFile(path, "r") as source:
        names = source.namelist()
        sheet_path = xlsx_sheet_path(source)
        if sheet_path not in names:
            abort(400, "Could not find worksheet")
        root = ET.fromstring(source.read(sheet_path))
        sheet_data = root.find(f"{{{main_ns}}}sheetData")
        if sheet_data is None:
            sheet_data = ET.Element(f"{{{main_ns}}}sheetData")
            insert_at = next((index for index, child in enumerate(root) if child.tag.endswith("dimension")), -1)
            root.insert(insert_at + 1 if insert_at >= 0 else 0, sheet_data)

        row_nodes = {
            int(node.attrib["r"]): node
            for node in sheet_data.findall(f"{{{main_ns}}}row")
            if node.attrib.get("r", "").isdigit()
        }
        target_width = max((len(row) for row in rows), default=1)
        for row_node in row_nodes.values():
            for cell in list(row_node.findall(f"{{{main_ns}}}c")):
                reference = cell.attrib.get("r", "")
                letters = "".join(character for character in reference if character.isalpha())
                column = 0
                for character in letters:
                    column = column * 26 + ord(character.upper()) - ord("A") + 1
                if column > target_width:
                    row_node.remove(cell)
        for row_index, values in enumerate(rows, start=1):
            row_node = row_nodes.get(row_index)
            if row_node is None:
                row_node = ET.Element(f"{{{main_ns}}}row", {"r": str(row_index)})
                existing_rows = sheet_data.findall(f"{{{main_ns}}}row")
                insert_at = next((index for index, node in enumerate(existing_rows) if int(node.attrib.get("r", "0")) > row_index), len(existing_rows))
                sheet_data.insert(insert_at, row_node)
                row_nodes[row_index] = row_node
            cells = {cell.attrib.get("r"): cell for cell in row_node.findall(f"{{{main_ns}}}c")}
            for column_index, value in enumerate(values):
                reference = f"{xlsx_column_name(column_index)}{row_index}"
                cell = cells.get(reference)
                if cell is None:
                    cell = ET.Element(f"{{{main_ns}}}c", {"r": reference})
                    existing_cells = row_node.findall(f"{{{main_ns}}}c")
                    insert_at = next(
                        (index for index, node in enumerate(existing_cells)
                         if node.attrib.get("r", "") and node.attrib["r"] > reference),
                        len(existing_cells),
                    )
                    row_node.insert(insert_at, cell)
                    cells[reference] = cell
                for child in list(cell):
                    cell.remove(child)
                cell.set("t", "inlineStr")
                inline = ET.SubElement(cell, f"{{{main_ns}}}is")
                text = ET.SubElement(inline, f"{{{main_ns}}}t")
                text.text = value

        max_existing_row = max(row_nodes, default=0)
        max_row = max(max_existing_row, len(rows))
        max_column = max(1, target_width)
        dimension = root.find(f"{{{main_ns}}}dimension")
        if dimension is None:
            dimension = ET.Element(f"{{{main_ns}}}dimension")
            root.insert(0, dimension)
        dimension.set("ref", f"A1:{xlsx_column_name(max_column - 1)}{max_row}")
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w") as output:
                for name in names:
                    payload = xml_bytes if name == sheet_path else source.read(name)
                    info = source.getinfo(name)
                    output.writestr(info, payload)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def table_rows(path: Path) -> list[list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return xlsx_table_rows(path)
    delimiter = "\t" if suffix == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as file:
        rows = list(csv.reader(file, delimiter=delimiter))
    return normalize_table_rows(rows)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/items")
def list_items():
    expirations = cleanup_expired()
    layout = load_layout()
    items = [item_payload(path, layout, expirations.get(path.name)) for path in DATA_DIR.iterdir() if not path.name.startswith(".")]
    items.sort(key=lambda item: item["modified"], reverse=True)
    return jsonify(items)


@app.get("/api/settings")
def get_settings():
    return jsonify({"autoDelete": auto_delete_enabled()})


@app.patch("/api/settings")
def update_settings():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("autoDelete"), bool):
        abort(400, "Invalid auto-delete setting")
    settings = load_settings()
    settings["autoDelete"] = payload["autoDelete"]
    save_settings(settings)
    if settings["autoDelete"]:
        reset_expirations()
        cleanup_expired()
    return jsonify({"autoDelete": settings["autoDelete"]})


@app.post("/api/upload")
def upload():
    cleanup_expired()
    files = request.files.getlist("files")
    if not files:
        abort(400, "No files uploaded")

    batch_dir = Path(tempfile.mkdtemp(prefix=".upload-", dir=DATA_DIR))
    try:
        folder_roots = {}
        final_roots = []
        reserved = set()
        for uploaded in files:
            relative = safe_relative_path(uploaded.filename or "untitled")
            if len(relative.parts) > 1:
                root = relative.parts[0]
                if root not in folder_roots:
                    folder_roots[root] = available_reserved_path(DATA_DIR / root, reserved)
                    final_roots.append(folder_roots[root])
                destination = folder_roots[root].joinpath(*relative.parts[1:])
            else:
                destination = available_reserved_path(DATA_DIR / relative.name, reserved)
                final_roots.append(destination)
            staged = batch_dir / destination.relative_to(DATA_DIR)
            staged.parent.mkdir(parents=True, exist_ok=True)
            uploaded.save(staged)

        created = []
        for final_path in final_roots:
            staged_path = batch_dir / final_path.relative_to(DATA_DIR)
            shutil.move(str(staged_path), str(final_path))
            created.append(item_payload(final_path, expires_at=set_expiration(final_path)))
        return jsonify(created), 201
    finally:
        shutil.rmtree(batch_dir, ignore_errors=True)


@app.post("/api/text")
def create_text():
    cleanup_expired()
    payload = request.get_json(silent=True) or {}
    content = payload.get("content", "")
    title = str(payload.get("title", "")).strip() or "未命名文字"
    if not isinstance(content, str) or not content.strip():
        abort(400, "Text is empty")
    if not title.lower().endswith(".txt"):
        title += ".txt"
    name = safe_relative_path(title).name
    destination = available_path(DATA_DIR / name)
    destination.write_text(content, encoding="utf-8")
    return jsonify(item_payload(destination, expires_at=set_expiration(destination))), 201


@app.post("/api/table")
def create_table():
    cleanup_expired()
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or not all(isinstance(row, list) for row in rows):
        abort(400, "Table is empty")
    normalized = normalize_table_rows(rows, max_rows=500, max_columns=50)
    if not normalized:
        abort(400, "Table is empty")
    title = str(payload.get("title", "")).strip() or "粘贴表格"
    if not title.lower().endswith(".csv"):
        title += ".csv"
    destination = available_path(DATA_DIR / safe_relative_path(title).name)
    with destination.open("w", encoding="utf-8-sig", newline="") as file:
        csv.writer(file).writerows(normalized)
    return jsonify(item_payload(destination, expires_at=set_expiration(destination))), 201


@app.get("/api/download/<path:item_path>")
def download(item_path: str):
    cleanup_expired()
    target = resolved_item(item_path)
    if not target.exists():
        abort(404)
    if target.is_file():
        return send_file(target, as_attachment=True, download_name=target.name)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in target.rglob("*"):
            if file.is_file():
                archive.write(file, file.relative_to(target.parent))
    buffer.seek(0)
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name=f"{target.name}.zip")


@app.get("/api/content/<path:item_path>")
def content(item_path: str):
    cleanup_expired()
    target = resolved_item(item_path)
    if not target.is_file():
        abort(404)
    return send_file(target, as_attachment=False, conditional=True, download_name=target.name)


@app.get("/api/preview/<path:item_path>")
def text_preview(item_path: str):
    cleanup_expired()
    target = resolved_item(item_path)
    if not target.is_file():
        abort(404)
    editable = target.suffix.lower() in {".md", ".markdown", ".txt"}
    limit = 8 * 1024 * 1024 if editable else 65536
    with target.open("r", encoding="utf-8", errors="replace") as file:
        preview = file.read(limit + 1)
    truncated = len(preview) > limit
    return jsonify({"content": preview[:limit], "truncated": truncated, "editable": editable and not truncated})


@app.get("/api/table/<path:item_path>")
def table_preview(item_path: str):
    cleanup_expired()
    target = resolved_item(item_path)
    if not target.is_file() or target.suffix.lower() not in TABLE_SUFFIXES:
        abort(400, "Unsupported table file")
    try:
        rows = table_rows(target)
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile):
        abort(400, "Could not read table")
    return jsonify({"rows": rows, "truncated": False, "editable": target.suffix.lower() == ".xlsx", "format": target.suffix.lower().lstrip(".")})


@app.patch("/api/table/<path:item_path>")
def update_table_preview(item_path: str):
    cleanup_expired()
    target = resolved_item(item_path)
    if not target.is_file() or target.suffix.lower() != ".xlsx":
        abort(400, "Only XLSX files can be edited")
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or not all(isinstance(row, list) for row in rows):
        abort(400, "Table is empty")
    try:
        save_xlsx_rows(target, rows)
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile):
        abort(400, "Could not save table")
    return jsonify(item_payload(target, load_layout(), load_expirations().get(target.name)))


@app.patch("/api/preview/<path:item_path>")
def update_text_preview(item_path: str):
    cleanup_expired()
    target = resolved_item(item_path)
    if not target.is_file() or target.suffix.lower() not in {".md", ".markdown", ".txt"}:
        abort(400, "Only Markdown and text files can be edited")
    payload = request.get_json(silent=True) or {}
    content = payload.get("content")
    if not isinstance(content, str):
        abort(400, "Invalid text content")
    if len(content.encode("utf-8")) > 8 * 1024 * 1024:
        abort(413, "Text is too large")
    target.write_text(content, encoding="utf-8")
    return jsonify(item_payload(target, load_layout(), load_expirations().get(target.name)))


@app.patch("/api/layout/<path:item_path>")
def update_layout(item_path: str):
    cleanup_expired()
    target = resolved_item(item_path)
    if not target.exists():
        abort(404)
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode")
    if mode not in {"desktop", "mobile"}:
        abort(400, "Invalid layout mode")

    limits = {"x": (0, 10000), "y": (0, 100000), "w": (120, 2000), "h": (120, 2000), "z": (1, 100000)}
    values = {}
    for key, (minimum, maximum) in limits.items():
        value = payload.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            abort(400, "Invalid layout value")
        values[key] = round(min(max(value, minimum), maximum), 2)

    layout = load_layout()
    path = target.relative_to(DATA_DIR).as_posix()
    layout.setdefault(path, {})[mode] = values
    save_layout(layout)
    return jsonify(values)


@app.delete("/api/items/<path:item_path>")
def delete_item(item_path: str):
    cleanup_expired()
    target = resolved_item(item_path)
    if not target.exists():
        abort(404)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    remove_expiration(target)
    layout = load_layout()
    layout.pop(safe_relative_path(item_path).as_posix(), None)
    save_layout(layout)
    return "", 204


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "文件超过上传大小限制"}), 413


def expiry_worker() -> None:
    while True:
        time.sleep(10)
        cleanup_expired()


threading.Thread(target=expiry_worker, name="expiry-cleanup", daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
