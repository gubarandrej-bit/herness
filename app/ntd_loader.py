"""Загрузчик нормативно-технической документации (НТД) из PDF-файлов."""
from __future__ import annotations

import re
from pathlib import Path
from . import config
from .db import db, utcnow
from pypdf import PdfReader


# Эталонный реестр НТД: код → (полное название, ожидаемая редакция, статус)
# При обнаружении файла с отличающейся редакцией система помечает документ
# как требующий проверки актуальности.
NTD_REGISTRY: dict[str, tuple[str, str, str]] = {
    "123-ФЗ": ("Технический регламент о требованиях пожарной безопасности", "2008", "active"),
    "СП 3.13130.2009": ("Системы противопожарной защиты. Система оповещения и управления эвакуацией", "2009", "superseded"),
    "СП 6.13130.2021": ("Электроустановки низковольтные. Требования пожарной безопасности", "2021", "superseded"),
    "СП 76.13330.2016": ("Электротехнические устройства", "2016", "active"),
    "СП 484.1311500.2020": ("Системы пожарной сигнализации и автоматизация", "2020", "active"),
    "СП 486.1311500.2020": ("Перечень зданий, подлежащих защите АУП и АПС", "2020", "active"),
    "ПУЭ": ("Правила устройства электроустановок", "7", "active"),
    "ГОСТ 21.208-2013": ("СПДС. Автоматизация технологических процессов. Обозначения", "2013", "active"),
    "ГОСТ Р 21.101-2026": ("СПДС. Основные требования к проектной и рабочей документации", "2026", "active"),
    "ГОСТ 21.210-2014": ("СПДС. Условные графические изображения электрооборудования и проводок", "2014", "active"),
    "ГОСТ Р 21.703-2020": ("СПДС. Правила выполнения рабочей документации проводных средств связи", "2020", "active"),
    "ГОСТ 31565-2012": ("Кабельные изделия. Требования пожарной безопасности", "2012", "active"),
    "ГОСТ Р 53246-2025": ("СКС. Проектирование основных узлов системы. Общие требования", "2025", "active"),
    "ГОСТ Р 58238-2018": ("Слаботочные системы. Кабельные системы. Порядок и нормы проектирования", "2018", "active"),
    "СП 48.13330.2019": ("Организация строительства", "2019", "active"),
    "СП 3.13130.2026": ("СОУЭ. Актуализированная редакция", "2026", "active"),
    "СП 6.13130.2025": ("Электроустановки низковольтные. Актуализированная редакция", "2025", "active"),
}

# Сопоставление имен файлов (по ключевым словам) с кодами НТД
FILE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"СП\s*3\.13130"), "СП 3.13130.2026"),
    (re.compile(r"СП\s*6\.13130"), "СП 6.13130.2025"),
    (re.compile(r"СП\s*76\.13330"), "СП 76.13330.2016"),
    (re.compile(r"СП\s*484"), "СП 484.1311500.2020"),
    (re.compile(r"СП\s*486"), "СП 486.1311500.2020"),
    (re.compile(r"СП\s*48\.13330"), "СП 48.13330.2019"),
    (re.compile(r"123[-_]ФЗ|123[-/]ФЗ|ФЗ_ПБ|Технический регламент"), "123-ФЗ"),
    (re.compile(r"ГОСТ\s*21\.208"), "ГОСТ 21.208-2013"),
    (re.compile(r"ГОСТ\s*Р?\s*21\.101"), "ГОСТ Р 21.101-2026"),
    (re.compile(r"ГОСТ\s*21\.210"), "ГОСТ 21.210-2014"),
    (re.compile(r"ГОСТ\s*Р?\s*21\.703"), "ГОСТ Р 21.703-2020"),
    (re.compile(r"ГОСТ\s*31565"), "ГОСТ 31565-2012"),
    (re.compile(r"ГОСТ\s*Р?\s*53246"), "ГОСТ Р 53246-2025"),
    (re.compile(r"ГОСТ\s*Р?\s*58238"), "ГОСТ Р 58238-2018"),
    (re.compile(r"ПУЭ"), "ПУЭ"),
]


def ensure_ntd_table() -> int:
    """Сканирует каталог docs/ntd, извлекает текст из PDF и заполняет таблицу ntd_documents.
    Возвращает количество обработанных файлов.
    """
    # Создаем каталог для извлечённых текстов
    texts_dir = config.DATA_DIR / "ntd_texts"
    texts_dir.mkdir(parents=True, exist_ok=True)

    src_dir = config.NTD_SOURCE_DIR
    if not src_dir.exists():
        return 0

    pdf_files = sorted(src_dir.glob("*.pdf"))
    count = 0

    for pdf_path in pdf_files:
        code = _match_code(pdf_path.name)
        if not code:
            continue

        entry = NTD_REGISTRY.get(code, (code, "", "unknown"))
        title, ref_revision, status = entry

        text = _extract_pdf_text(pdf_path)
        text_path = texts_dir / f"{code}.txt"
        text_path.write_text(text, encoding="utf-8")

        pages = 0
        try:
            reader = PdfReader(str(pdf_path))
            pages = len(reader.pages)
        except Exception:
            pages = 0

        with db() as conn:
            existing = conn.execute(
                "SELECT id FROM ntd_documents WHERE code = ?", (code,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE ntd_documents SET title=?, revision=?, status=?,
                       source_file=?, text_extracted=1, text_chars=?, pages=?,
                       text_path=?, updated_at=? WHERE code=?""",
                    (title, ref_revision, status, pdf_path.name, len(text),
                     pages, str(text_path), utcnow(), code),
                )
            else:
                conn.execute(
                    """INSERT INTO ntd_documents
                       (code, title, revision, status, source_file, text_extracted,
                        text_chars, pages, text_path, checked_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
                    (code, title, ref_revision, status, pdf_path.name, len(text),
                     pages, str(text_path), utcnow(), utcnow(), utcnow()),
                )
            # Обновляем статус актуальности для superseded документов
            if status == "superseded":
                conn.execute(
                    "UPDATE ntd_documents SET status = 'superseded' WHERE code LIKE ?",
                    (code.rsplit(".", 1)[0] + ".%",),
                )
        count += 1
    return count


def check_currency(conn, doc_id: int):
    """Проверяет актуальность одного документа и обновляет его статус."""
    row = conn.execute(
        "SELECT code, revision FROM ntd_documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if not row:
        return
    code, revision = row["code"], row["revision"]
    base = code.rsplit(".", 1)[0] if "." in code else code
    # Проверяем по реестру: если для этого базового кода есть более новая редакция
    for reg_code, (_, reg_rev, reg_status) in NTD_REGISTRY.items():
        reg_base = reg_code.rsplit(".", 1)[0] if "." in reg_code else reg_code
        if reg_base == base and reg_rev > revision and reg_status == "active":
            conn.execute(
                "UPDATE ntd_documents SET status = 'superseded', superseded_by = ?, checked_at = ? WHERE id = ?",
                (reg_code, utcnow(), doc_id),
            )
            return
    conn.execute(
        "UPDATE ntd_documents SET status = 'active', checked_at = ? WHERE id = ?",
        (utcnow(), doc_id),
    )


def _match_code(filename: str) -> str | None:
    """Определяет код НТД по имени файла."""
    for pattern, code in FILE_PATTERNS:
        if pattern.search(filename):
            return code
    return None


def _extract_pdf_text(pdf_path: Path) -> str:
    """Извлекает текст из PDF-файла."""
    try:
        reader = PdfReader(str(pdf_path))
        lines = []
        for page in reader.pages:
            text = page.extract_text() or ""
            lines.append(text)
        return "\n".join(lines)
    except Exception:
        return ""


def get_ntd_list() -> list[dict]:
    """Возвращает список НТД из БД."""
    with db() as conn:
        rows = conn.execute(
            """SELECT id, code, title, revision, status, superseded_by, pages, checked_at
               FROM ntd_documents ORDER BY code"""
        ).fetchall()
        return [dict(r) for r in rows]


def search_ntd(query: str, limit: int = 5) -> list[dict]:
    """Поиск документов НТД по тексту."""
    with db() as conn:
        rows = conn.execute(
            """SELECT id, code, title, revision, status FROM ntd_documents
               WHERE code LIKE ? OR title LIKE ? LIMIT ?""",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]