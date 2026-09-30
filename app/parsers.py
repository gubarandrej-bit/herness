"""Парсеры входящих документов: XLSX, DOCX, PDF, DWG (заглушка)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
from docx import Document

# pypdf импортируется только при необходимости — в Docker он есть,
# но на голом Python без него парсеры не должны падать при импорте.


def parse_file(file_path: str) -> dict[str, Any]:
    """Определяет тип файла и возвращает структурированное содержимое."""
    ext = Path(file_path).suffix.lower()
    if ext in (".xls", ".xlsx", ".xlsm"):
        return _parse_xlsx(file_path)
    elif ext in (".doc", ".docx"):
        return _parse_docx(file_path)
    elif ext == ".pdf":
        return _parse_pdf(file_path)
    elif ext in (".dwg", ".dxf"):
        return _parse_dwg(file_path)
    return {"error": f"Неподдерживаемый формат: {ext}", "raw": ""}


def _parse_docx(path: str) -> dict[str, Any]:
    """Извлекает текст из DOCX."""
    try:
        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        # Таблицы
        tables = []
        for table in doc.tables:
            rows = []
            for row in table.rows:
                rows.append([cell.text.strip() for cell in row.cells])
            if rows:
                tables.append(rows)
        return {
            "format": "docx",
            "paragraphs": paragraphs,
            "tables": tables,
            "text": "\n".join(paragraphs),
            "rows": len(paragraphs),
        }
    except Exception as e:
        return {"error": str(e), "raw": "", "format": "docx"}


def _parse_xlsx(path: str) -> dict[str, Any]:
    """Извлекает данные из XLSX с помощью pandas."""
    try:
        result = {"format": "xlsx", "sheets": {}, "tables": []}
        xl = pd.ExcelFile(path)
        for sheet_name in xl.sheet_names:
            df = xl.parse(sheet_name, dtype=str)
            df = df.fillna("")
            # Определяем тип содержимого по заголовкам
            headers = [str(c).lower() for c in df.columns]
            sheet_type = _detect_sheet_type(headers)
            data = df.to_dict(orient="records")
            result["sheets"][sheet_name] = {
                "headers": list(df.columns),
                "rows": len(data),
                "type": sheet_type,
                "data": data,
            }
            # Плоские таблицы для анализа
            result["tables"].append({
                "name": sheet_name,
                "type": sheet_type,
                "headers": list(df.columns),
                "data": data,
            })
        return result
    except Exception as e:
        return {"error": str(e), "format": "xlsx", "sheets": {}}


def _parse_pdf(path: str) -> dict[str, Any]:
    """Извлекает текст из PDF."""
    from pypdf import PdfReader  # lazy import — pypdf есть только в Docker
    try:
        reader = PdfReader(path)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append({"page": i + 1, "text": text})
        return {
            "format": "pdf",
            "pages": pages,
            "text": "\n".join(p["text"] for p in pages),
            "page_count": len(pages),
        }
    except Exception as e:
        return {"error": str(e), "format": "pdf", "text": ""}


def _parse_dwg(path: str) -> dict[str, Any]:
    """Заглушка для DWG. Реальная поддержка требует ODA File Converter или аналога."""
    return {
        "format": "dwg",
        "error": "Формат DWG не поддерживается напрямую. "
                 "Требуется конвертация в DXF через ODA File Converter (установить отдельно) или загрузка DXF вместо DWG.",
        "warnings": [],
    }


def _detect_sheet_type(headers: list[str]) -> str:
    """Определяет тип листа по заголовкам столбцов."""
    h = " ".join(headers).lower()
    if any(k in h for k in ["кабель", "марка", "сечение", "длина", "трасса", "журнал"]):
        return "cable_journal"
    if any(k in h for k in ["поз", "наименование", "оборудование", "материал", "ед."]):
        return "specification"
    if any(k in h for k in ["расчёт", "нагрузк", "ток", "мощность", "ип"]):
        return "calculation"
    if any(k in h for k in ["план", "лист", "трасс"]):
        return "plan"
    return "other"


def extract_cable_journal(data: dict) -> list[dict]:
    """Извлекает кабельный журнал из распарсенных данных."""
    cables = []
    for table in data.get("tables", []):
        if table["type"] != "cable_journal":
            continue
        for row in table["data"]:
            cables.append(_normalize_cable_row(row, table["headers"]))
    return cables


def extract_specification(data: dict) -> list[dict]:
    """Извлекает спецификацию оборудования и материалов."""
    items = []
    for table in data.get("tables", []):
        if table["type"] != "specification":
            continue
        for row in table["data"]:
            items.append(_normalize_spec_row(row, table["headers"]))
    return items


def extract_calculations(data: dict) -> list[dict]:
    """Извлекает расчёты."""
    calcs = []
    for table in data.get("tables", []):
        if table["type"] != "calculation":
            continue
        calc = {"sheet_name": table["name"], "headers": table["headers"], "rows": table["data"]}
        calcs.append(calc)
    return calcs


def _normalize_cable_row(row: dict, headers: list[str]) -> dict:
    """Нормализует строку кабельного журнала."""
    result = {}
    for h in headers:
        h_lower = h.lower()
        val = str(row.get(h, "")).strip()
        if any(k in h_lower for k in ["марка", "тип", "кабель"]):
            result["marka"] = val
        elif any(k in h_lower for k in ["сечение", "мм"]):
            result["section"] = val
        elif "длин" in h_lower:
            result["length"] = val
        elif "трасс" in h_lower:
            result["route"] = val
        elif any(k in h_lower for k in ["обозначение", "номер"]):
            result["designation"] = val
        elif "наименование" in h_lower:
            result["name"] = val
        elif "количество" in h_lower or "кол-во" in h_lower:
            result["quantity"] = val
    result["_raw"] = row
    return result


def _normalize_spec_row(row: dict, headers: list[str]) -> dict:
    """Нормализует строку спецификации."""
    result = {}
    for h in headers:
        h_lower = h.lower()
        val = str(row.get(h, "")).strip()
        if "поз" == h_lower or "поз." == h_lower:
            result["pos"] = val
        elif any(k in h_lower for k in ["наименование", "оборудование", "материал"]):
            result["name"] = val
        elif any(k in h_lower for k in ["тип", "марка"]):
            result["type"] = val
        elif any(k in h_lower for k in ["количество", "кол-во", "шт"]):
            result["quantity"] = val
        elif "ед" in h_lower and "изм" in h_lower:
            result["unit"] = val
        elif any(k in h_lower for k in ["примечание", "прим"]):
            result["note"] = val
    result["_raw"] = row
    return result