# ruff: noqa: RUF001
"""Short completion captions and a validated pointer to the saved report."""

import re
from typing import TYPE_CHECKING

from legal_core.contracts import CanonicalReport, ReportResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

if TYPE_CHECKING:
    from telegram_gateway.analysis_jobs_runtime import JobSnapshot


def ready_report(job: "JobSnapshot") -> ReportResponse | None:
    if job.state != "SUCCEEDED" or job.result is None:
        return None
    if job.result_version == 2:
        if job.result.get("status") != "READY":
            return None
    elif job.result.get("analysisAllowed") is not True:
        return None
    report = ReportResponse.model_validate(job.result.get("report"))
    canonical = CanonicalReport.model_validate(report.report_json)
    if (report.case_id != job.case_id or canonical.case.id != job.case_id
            or canonical.report_id != report.id):
        raise ValueError("completed report identity mismatch")
    if (canonical.summary.analysis_availability.status != "READY"
            or canonical.analysis is None or canonical.analysis.verifier_status != "PASSED"):
        raise ValueError("completed report has no verified result")
    return report


def report_caption(report: ReportResponse) -> tuple[str, str]:
    number = report.report_json.get("case", {}).get("publicNumber")
    if not isinstance(number, str) or re.fullmatch(r"DL-[A-Z0-9-]{1,40}", number) is None:
        return "✅ Отчёт готов. PDF прикреплён.", "otchet.pdf"
    return f"✅ Кейс {number}\nОтчёт готов. PDF прикреплён.", f"{number}-otchet.pdf"


def report_keyboard(case_id: object) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Открыть кейс", callback_data=f"history:view:{case_id}:0")],
        [InlineKeyboardButton("📂 Мои кейсы", callback_data="history:list:0")],
    ])
