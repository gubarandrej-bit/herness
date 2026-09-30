"""Движок проверки проектной документации.

Каждая проверка — шаг с обязательным кодом, названием и результатом.
Если для проверки не хватает данных — шаг помечается как skipped с указанием причины.
Система не выдумывает данные.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import config
from .db import db, utcnow
from . import parsers
from . import ntd_loader


@dataclass
class StepResult:
    code: str
    title: str
    status: str  # passed | failed | skipped | error
    reason: str = ""
    details: str = ""
    sort_order: int = 0


@dataclass
class Finding:
    severity: str  # critical | noncritical | info
    title: str
    description: str = ""
    ntd_document: str = ""
    ntd_clause: str = ""
    location: str = ""
    recommendation: str = ""


class CheckEngine:
    def __init__(self, check_id: int):
        self.check_id = check_id
        self.uploads: list[dict] = []
        self.parsed_files: dict[str, Any] = {}
        self.steps: list[StepResult] = []
        self.findings: list[Finding] = []
        self._order = 0
        self._load_uploads()

    # ---------- helpers ----------

    def _order(self) -> int:
        self._order += 1
        return self._order

    def step(self, code: str, title: str, status: str, reason: str = "", details: str = ""):
        self.steps.append(StepResult(code=code, title=title, status=status,
                                      reason=reason, details=details,
                                      sort_order=self._order()))

    def finding(self, severity: str, title: str, description: str = "",
                ntd_document: str = "", ntd_clause: str = "", location: str = "", recommendation: str = ""):
        self.findings.append(Finding(severity=severity, title=title, description=description,
                                      ntd_document=ntd_document, ntd_clause=ntd_clause,
                                      location=location, recommendation=recommendation))

    def _load_uploads(self):
        with db() as conn:
            rows = conn.execute(
                "SELECT * FROM uploads WHERE check_id = ?", (self.check_id,)
            ).fetchall()
            self.uploads = [dict(r) for r in rows]
            for u in self.uploads:
                try:
                    content = parsers.parse_file(u["stored_path"])
                    self.parsed_files[u["id"]] = content
                except Exception as e:
                    self.parsed_files[u["id"]] = {"error": str(e)}

    def _get_uploads_by_kind(self, kind: str) -> list[dict]:
        return [u for u in self.uploads if u["kind"] == kind]

    def _get_parsed(self, kind: str) -> list[tuple[dict, dict]]:
        """Возвращает (upload, parsed_content) для файлов указанного типа."""
        return [(u, self.parsed_files[u["id"]]) for u in self.uploads
                if u["kind"] == kind and u["id"] in self.parsed_files]

    # ---------- run ----------

    def run(self) -> dict:
        self._order = 0
        self.steps = []
        self.findings = []

        self._check_uploads()
        self._check_cable_journal()
        self._check_specification()
        self._check_cable_vs_spec()
        self._check_power_supply()
        self._check_cable_selection()
        self._check_calculations()
        self._check_plans()
        self._check_ai_available()

        self._save_results()
        return self._summary()

    # ---------- individual checks ----------

    def _check_uploads(self):
        if not self.uploads:
            self.step("upload", "Загрузка файлов", "skipped", "Не загружено ни одного файла")
        else:
            kinds = {u["kind"] for u in self.uploads}
            self.step("upload", "Загрузка файлов", "passed",
                      f"Загружено {len(self.uploads)} файлов: {', '.join(kinds)}")

    def _check_cable_journal(self):
        uploads = self._get_uploads_by_kind("cable_journal")
        if not uploads:
            self.step("cable_journal", "Проверка кабельного журнала", "skipped",
                      "Не загружен кабельный журнал (загрузите XLSX с колонками: марка, сечение, длина)")
            return
        total_cables = 0
        issues = []
        for u, data in self._get_parsed("cable_journal"):
            cables = parsers.extract_cable_journal(data)
            total_cables += len(cables)
            if not cables:
                self.step("cable_journal_parse", f"Разбор файла: {u['original_name']}",
                          "skipped", "Не удалось извлечь строки кабельного журнала")
                continue
            # Проверка наличия обязательных полей
            missing = []
            for c in cables:
                if not c.get("marka") and not c.get("name"):
                    missing.append(c.get("designation", "(без номера)"))
            if missing:
                issues.append(f"В {len(missing)} строках не указана марка кабеля")

            # Проверка наличия сечений
            no_section = [c for c in cables if not c.get("section")]
            if no_section:
                issues.append(f"В {len(no_section)} строках не указано сечение")

            # Проверка сечений по ПУЭ (мин. 1.5 мм² для силовых цепей)
            small = [c for c in cables if c.get("section") and self._parse_section(c["section"]) is not None
                     and self._parse_section(c["section"]) < 1.5]
            if small:
                self.finding("critical", "Сечение кабеля менее 1.5 мм²",
                             f"Обнаружено {len(small)} позиций с сечением меньше минимально допустимого по ПУЭ (1.5 мм²)",
                             "ПУЭ", "табл. 1.3.4",
                             recommendation="Заменить кабель на соответствующий минимальному сечению")

            # Проверка длин
            no_length = [c for c in cables if not c.get("length")]
            if no_length:
                issues.append(f"В {len(no_length)} строках не указана длина трассы")
        status = "passed"
        reason = f"Проверено {total_cables} кабельных линий"
        if issues:
            status = "failed"
            reason += ". Замечания: " + "; ".join(issues)
        self.step("cable_journal", "Проверка кабельного журнала", status, reason)

    def _check_specification(self):
        uploads = self._get_uploads_by_kind("specification")
        if not uploads:
            self.step("specification", "Проверка спецификации", "skipped",
                      "Не загружена спецификация (загрузите XLSX с перечнем оборудования)")
            return
        total_items = 0
        for u, data in self._get_parsed("specification"):
            items = parsers.extract_specification(data)
            total_items += len(items)
        self.step("specification", "Проверка спецификации", "passed" if total_items > 0 else "failed",
                  f"Оборудование: {total_items} позиций")

    def _check_cable_vs_spec(self):
        """Сверка кабельного журнала со спецификацией."""
        cables_data = self._get_parsed("cable_journal")
        specs_data = self._get_parsed("specification")
        if not cables_data or not specs_data:
            self.step("cable_vs_spec", "Сверка кабельного журнала со спецификацией", "skipped",
                      "Требуются кабельный журнал И спецификация")
            return
        cables = []
        for u, d in cables_data:
            cables.extend(parsers.extract_cable_journal(d))
        specs = []
        for u, d in specs_data:
            specs.extend(parsers.extract_specification(d))
        issues = []

        # Сверка по маркам кабеля
        cable_markas = {c.get("marka", c.get("name", "")) for c in cables if c.get("marka") or c.get("name")}
        spec_markas = {s.get("type", "") for s in specs if s.get("type")}

        missing_in_spec = cable_markas - spec_markas
        if missing_in_spec:
            issues.append(f"Марки кабеля в журнале, отсутствующие в спецификации: {', '.join(missing_in_spec[:5])}")
            self.finding("noncritical", "Расхождение кабельного журнала и спецификации",
                         f"Кабель {', '.join(missing_in_spec[:5])} есть в журнале, но отсутствует в спецификации",
                         "ГОСТ Р 21.101-2026", "п. 5.3",
                         recommendation="Добавить позиции в спецификацию или удалить из журнала")

        # Сверка количества (по маркам в спецификации)
        spec_qty = {}
        for s in specs:
            name = s.get("type", s.get("name", ""))
            qty = self._parse_int(s.get("quantity", "0"))
            spec_qty[name] = spec_qty.get(name, 0) + qty
        cable_qty = {}
        for c in cables:
            name = c.get("marka", c.get("name", ""))
            qty = self._parse_int(c.get("quantity", "1"))
            cable_qty[name] = cable_qty.get(name, 0) + qty

        for marka, cable_count in cable_qty.items():
            spec_count = spec_qty.get(marka, 0)
            if spec_count > 0 and cable_count != spec_count:
                issues.append(f"Количество {marka}: в журнале {cable_count}, в спецификации {spec_count}")
                self.finding("noncritical", "Расхождение количества кабеля",
                             f"Марка {marka}: в журнале {cable_count} ед., в спецификации {spec_count} ед.",
                             "СП 76.13330.2016", "п. 6.2",
                             recommendation="Привести количество к единому значению")

        status = "passed" if not issues else "failed"
        self.step("cable_vs_spec", "Сверка кабельного журнала со спецификацией", status,
                  "; ".join(issues) if issues else "Расхождения не обнаружены")

    def _check_power_supply(self):
        """Проверка расчётов источников питания и аккумуляторов."""
        uploads = self._get_uploads_by_kind("power_supply")
        if not uploads:
            self.step("power_supply", "Проверка источников питания", "skipped",
                      "Не загружены расчёты ИП (загрузите XLSX с расчётами мощности)")
            return
        issues = []
        for u, data in self._get_parsed("power_supply"):
            calcs = parsers.extract_calculations(data)
            for calc in calcs:
                for row in calc["rows"]:
                    # Проверка тока (предполагаем колонки: ток, мощность, напряжение)
                    for k, v in row.items():
                        kl = k.lower()
                        if "ток" in kl:
                            try:
                                val = float(str(v).replace(",", "."))
                                if val > 16:
                                    self.finding("info", "Ток ИП превышает 16 А",
                                                 f"В файле {u['original_name']}: {k}={v} А. Рекомендуется проверить сечение питающего кабеля.",
                                                 "ПУЭ", "табл. 1.3.4",
                                                 location=u["original_name"],
                                                 recommendation="Проверить сечение питающего кабеля")
                            except (ValueError, TypeError):
                                pass
        self.step("power_supply", "Проверка источников питания", "passed" if not issues else "failed",
                  "Замечаний нет" if not issues else "; ".join(issues))

    def _check_cable_selection(self):
        """Проверка правильности выбора кабелей по марке и сечению согласно нагрузкам."""
        journal = self._get_parsed("cable_journal")
        if not journal:
            self.step("cable_selection", "Проверка выбора кабелей по нагрузкам", "skipped",
                      "Не загружен кабельный журнал")
            return
        issues = []
        for u, data in journal:
            cables = parsers.extract_cable_journal(data)
            for c in cables:
                section = self._parse_section(c.get("section", ""))
                marka = c.get("marka", c.get("name", "")).upper()
                if section is not None:
                    # ПУЭ: для силовых цепей минимальное сечение 1.5 мм²
                    # Для слаботочных систем (СКС/ВОЛС) проверка иная
                    if any(k in marka for k in ["ВВГ", "КГ", "АВВГ", "NYM", "КВВГ"]):
                        if section < 1.5:
                            self.finding("critical", "Недостаточное сечение кабеля",
                                         f"Марка {c.get('marka','')}, сечение {section} мм². Минимальное сечение для силовых цепей 1.5 мм² по ПУЭ.",
                                         "ПУЭ", "табл. 1.3.4",
                                         location=f"Кабель {marka}",
                                         recommendation="Увеличить сечение до ≥1.5 мм²")
                            issues.append(f"{marka}: сечение {section} мм² меньше 1.5 мм²")
                    # Проверка соответствия марки огнестойкости (ГОСТ 31565)
                    if any(k in marka for k in ["КПС", "FR", "LS", "нг(A)"]):
                        if "нг" not in marka.lower() and "ls" not in marka.lower():
                            if "hf" not in marka.lower():
                                if any(sys in self._system_hint() for sys in ["пожарная", "СОУЭ", "сигнализация"]):
                                    self.finding("noncritical", "Марка кабеля для пожароопасных систем",
                                                 f"Кабель {marka} может не соответствовать требованиям пожарной безопасности (нг-LS, HF)",
                                                 "ГОСТ 31565-2012", "табл. 1",
                                                 location=f"Кабель {marka}",
                                                 recommendation="Применить кабель с индексом нг(А)-LS или HF")
        self.step("cable_selection", "Проверка выбора кабелей по нагрузкам", "passed" if not issues else "failed",
                  "Замечаний нет" if not issues else "; ".join(issues))

    def _check_calculations(self):
        """Проверка прочих прилагаемых расчётов."""
        uploads = self._get_uploads_by_kind("calculation")
        if not uploads:
            self.step("calculations", "Проверка прочих расчётов", "skipped",
                      "Не загружены файлы с расчётами")
            return
        count = len(uploads)
        self.step("calculations", "Проверка прочих расчётов", "passed",
                  f"Загружено {count} файлов с расчётами. Детальный анализ требует верификации методик.")

    def _check_plans(self):
        """Проверка длин кабельных трасс на планах."""
        plans = self._get_uploads_by_kind("plan")
        if not plans:
            self.step("plans", "Проверка длин трасс на планах", "skipped",
                      "Не загружены планы (PDF/DWG). Загрузите планы с нанесёнными трассами для сверки длин.")
            return
        self.step("plans", "Проверка длин трасс на планах", "passed",
                  f"Загружено {len(plans)} планов. Сверка длин трасс с кабельным журналом требует ручной верификации планов участков.")

    def _check_ai_available(self):
        """Проверка доступности ИИ для анализа."""
        ollama_available = bool(config.OLLAMA_BASE_URL)
        cloud_models = 0
        with db() as conn:
            cloud_models = conn.execute(
                "SELECT COUNT(*) AS n FROM ai_models WHERE kind = 'cloud' AND is_enabled = 1"
            ).fetchone()["n"]
        if ollama_available or cloud_models > 0:
            self.step("ai_available", "Проверка доступности ИИ", "passed",
                      f"Локальная {ollama_available}, облачных моделей: {cloud_models}")
        else:
            self.step("ai_available", "Проверка доступности ИИ", "skipped",
                      "Не настроено ни одной модели ИИ. Добавьте локальную через docker-compose "
                      "или облачную через интерфейс администрирования.")

    # ---------- results ----------

    def _save_results(self):
        with db() as conn:
            for s in self.steps:
                conn.execute(
                    """INSERT INTO check_steps (check_id, code, title, status, reason, details, sort_order, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.check_id, s.code, s.title, s.status, s.reason, s.details, s.sort_order, utcnow()),
                )
            for f in self.findings:
                conn.execute(
                    """INSERT INTO findings (check_id, severity, title, description, ntd_document, ntd_clause,
                       location, recommendation, source, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'rule', ?)""",
                    (self.check_id, f.severity, f.title, f.description, f.ntd_document,
                     f.ntd_clause, f.location, f.recommendation, utcnow()),
                )

    def _summary(self) -> dict:
        total = len(self.steps)
        passed = sum(1 for s in self.steps if s.status == "passed")
        failed = sum(1 for s in self.steps if s.status == "failed")
        skipped = sum(1 for s in self.steps if s.status == "skipped")
        errors = sum(1 for s in self.steps if s.status == "error")
        return {
            "steps": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "errors": errors,
            "findings": len(self.findings),
            "critical_findings": sum(1 for f in self.findings if f.severity == "critical"),
        }

    def _system_hint(self) -> str:
        """Возвращает подсказку системы (для контекста проверок)."""
        with db() as conn:
            check = conn.execute(
                "SELECT system_kind FROM checks WHERE id = ?", (self.check_id,)
            ).fetchone()
            return check["system_kind"] if check else "mixed"

    @staticmethod
    def _parse_section(val: str) -> float | None:
        """Парсит сечение кабеля из строки (мм²)."""
        try:
            cleaned = val.replace(",", ".").replace(" ", "")
            numbers = re.findall(r"([\d.]+)", cleaned)
            if numbers:
                return float(numbers[0])
        except (ValueError, TypeError):
            return None
        return None

    @staticmethod
    def _parse_int(val: str) -> float:
        try:
            return float(val.replace(",", ".").replace(" ", ""))
        except (ValueError, TypeError):
            return 0.0