"""Генерация отчётов по результатам проверки в форматах DOCX и XLSX."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from . import config
from .db import db, utcnow


def _load_check_data(check_id: int) -> dict | None:
    """Загружает данные проверки из БД."""
    with db() as conn:
        check = conn.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
        if not check:
            return None
        findings = conn.execute("SELECT * FROM findings WHERE check_id = ? ORDER BY id", (check_id,)).fetchall()
        steps = conn.execute("SELECT * FROM check_steps WHERE check_id = ? ORDER BY sort_order", (check_id,)).fetchall()
        uploads = conn.execute("SELECT * FROM uploads WHERE check_id = ?", (check_id,)).fetchall()
        return {
            "check": dict(check),
            "findings": [dict(r) for r in findings],
            "steps": [dict(r) for r in steps],
            "uploads": [dict(r) for r in uploads],
        }


def _criticality(severity: str) -> str:
    return {"critical": "Критическое", "noncritical": "Некритическое", "info": "Информационное"}.get(severity, severity)


# ================================================================
#  DOCX report
# ================================================================

def generate_docx(check_id: int) -> Path | None:
    """Генерирует отчёт в формате DOCX. Возвращает путь к файлу."""
    data = _load_check_data(check_id)
    if not data:
        return None

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "DejaVu Sans"
    style.font.size = Pt(10)

    # Титул
    title = doc.add_heading("Отчёт о проверке документации", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    info = data["check"]
    doc.add_paragraph(f"Название проверки: {info.get('name', '—')}")
    doc.add_paragraph(f"Дата: {info.get('finished_at', info.get('created_at', '—'))}")
    doc.add_paragraph(f"Режим: {info.get('mode', '—')}")
    doc.add_paragraph(f"Система: {info.get('system_kind', '—')}")
    summary = info.get("summary", "{}")
    try:
        s = json.loads(summary) if isinstance(summary, str) else summary
    except json.JSONDecodeError:
        s = {}
    doc.add_paragraph(f"Статус: {info.get('status', '—')}")
    doc.add_paragraph("")

    # Статистика
    doc.add_heading("Сводка", level=2)
    p = doc.add_paragraph()
    p.add_run(f"Всего проверок: {s.get('steps', '—')}\n").bold = True
    p.add_run(f"Пройдено: {s.get('passed', 0)}\n")
    p.add_run(f"Замечаний: {s.get('findings', 0)}, из них критических: {s.get('critical_findings', 0)}\n")
    p.add_run(f"Пропущено: {s.get('skipped', 0)}\n")

    # Перечень выполненных/пропущенных проверок
    doc.add_heading("Перечень проверок", level=2)
    for step in data["steps"]:
        p = doc.add_paragraph()
        run = p.add_run(f"[{step['status'].upper()}] {step['title']}")
        if step["status"] == "passed":
            run.font.color = RGBColor(0x3f, 0xb9, 0x50)
        elif step["status"] == "failed":
            run.font.color = RGBColor(0xf8, 0x51, 0x49)
        elif step["status"] == "skipped":
            run.font.color = RGBColor(0x8b, 0x94, 0x9e)
        if step.get("reason"):
            doc.add_paragraph(f"   {step['reason']}", style="List Bullet")

    # Замечания
    doc.add_heading("Замечания и выводы", level=2)
    findings = data["findings"]
    if not findings:
        doc.add_paragraph("Замечаний не выявлено.")
    else:
        for f in findings:
            sev = _criticality(f["severity"])
            p = doc.add_paragraph()
            run = p.add_run(f"[{sev}] {f['title']}")
            if f["severity"] == "critical":
                run.font.color = RGBColor(0xf8, 0x51, 0x49)
            else:
                run.font.color = RGBColor(0xd2, 0x99, 0x22)
            if f.get("description"):
                doc.add_paragraph(f"  Описание: {f['description']}")
            if f.get("ntd_document"):
                ntd_text = f"  Ссылка: {f['ntd_document']}"
                if f.get("ntd_clause"):
                    ntd_text += f", п. {f['ntd_clause']}"
                doc.add_paragraph(ntd_text)
            if f.get("location"):
                doc.add_paragraph(f"  Расположение: {f['location']}")
            if f.get("recommendation"):
                doc.add_paragraph(f"  Рекомендация: {f['recommendation']}")
            doc.add_paragraph("")

    # Ведомость объёмов работ (из uploads)
    doc.add_heading("Ведомость объёмов работ", level=2)
    table = doc.add_table(rows=1, cols=4)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for i, text in enumerate(["Файл", "Тип", "Размер (КБ)", "Статус парсинга"]):
        hdr[i].text = text
    for u in data["uploads"]:
        row = table.add_row().cells
        row[0].text = u.get("original_name", "—")
        row[1].text = u.get("kind", "—")
        row[2].text = str(round(u.get("size_bytes", 0) / 1024, 1))
        row[3].text = "OK" if u.get("parsed_ok") else ("Ошибка" if u.get("parse_error") else "Не разобран")

    # Ведомость оборудования и материалов (заглушка, т.к. данные в parsed_data)
    doc.add_heading("Ведомость оборудования и материалов", level=2)
    doc.add_paragraph("Ведомость формируется на основе загруженной спецификации.")

    # Сохранение
    out = config.REPORTS_DIR / f"report_{check_id}.docx"
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


# ================================================================
#  XLSX report
# ================================================================

def generate_xlsx(check_id: int) -> Path | None:
    """Генерирует отчёт в формате XLSX. Возвращает путь к файлу."""
    data = _load_check_data(check_id)
    if not data:
        return None

    wb = Workbook()
    hdr_font = Font(bold=True, color="FFFFFF")
    hdr_fill = PatternFill(start_color="21262D", end_color="21262D", fill_type="solid")
    border = Border(
        left=Side(style="thin", color="30363D"),
        right=Side(style="thin", color="30363D"),
        top=Side(style="thin", color="30363D"),
        bottom=Side(style="thin", color="30363D"),
    )

    def style_header(ws, row=1):
        for cell in ws[row]:
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal="center")
            cell.border = border

    # Лист 1: Перечень проверок
    ws1 = wb.active
    ws1.title = "Проверки"
    ws1.append(["Код", "Название", "Статус", "Описание"])
    style_header(ws1)
    for step in data["steps"]:
        ws1.append([step["code"], step["title"], step["status"], step["reason"]])

    # Лист 2: Замечания
    ws2 = wb.create_sheet("Замечания")
    ws2.append(["Важность", "Заголовок", "Описание", "НТД", "Пункт", "Расположение", "Рекомендация"])
    style_header(ws2)
    for f in data["findings"]:
        ws2.append([
            _criticality(f["severity"]),
            f["title"],
            f["description"],
            f["ntd_document"],
            f["ntd_clause"],
            f["location"],
            f["recommendation"],
        ])

    # Лист 3: Файлы
    ws3 = wb.create_sheet("Файлы")
    ws3.append(["Имя", "Тип", "Размер (КБ)", "Формат"])
    style_header(ws3)
    for u in data["uploads"]:
        ws3.append([u["original_name"], u["kind"], round(u["size_bytes"] / 1024, 1), u["extension"]])

    # Настройка ширины колонок
    for ws in wb.worksheets:
        for col in ws.columns:
            max_len = min(max((len(str(c.value or "")) for c in col), default=10) + 3, 60)
            ws.column_dimensions[col[0].column_letter].width = max_len

    out = config.REPORTS_DIR / f"report_{check_id}.xlsx"
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(str(out))
    return out