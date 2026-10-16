#!/usr/bin/env python3
"""Validate an analyzer result and build the customer PDF + XLSX package."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    from .taxonomy_integrity import verify_taxonomy
except ImportError:  # Running this file directly from the scripts directory.
    from taxonomy_integrity import verify_taxonomy


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = ROOT / "설계" / "03_업종분류표.json"
FONT_PATH = Path(r"C:\Windows\Fonts\malgun.ttf")
RADII = ("1km", "2km", "3km", "4km", "5km")
SORT_RADII = ("5km", "4km", "3km", "2km", "1km")
NAVY = "#17324D"
BLUE = "#E8EEF5"
INK = "#172B4D"
MUTED = "#556579"
GRID = "#D8E0E8"
PALE = "#F5F7FA"
ORANGE = "#9A3412"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    counts = item["counts"]
    return (*[int(counts[radius]) for radius in SORT_RADII], str(item["code"]))


def validate_result(data: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    taxonomy_hash = verify_taxonomy(taxonomy)
    _require(data.get("status") == "analysis_complete", "분석 상태가 complete가 아닙니다.")
    _require(data.get("publishable") is True and data.get("publication_gate") == "passed", "발행 게이트를 통과하지 않았습니다.")
    source = data.get("source") or {}
    _require(source.get("taxonomy_version") == taxonomy.get("taxonomy_version"), "분석 결과와 현재 taxonomy 버전이 다릅니다.")
    _require(source.get("taxonomy_hash") == taxonomy_hash, "분석 결과와 현재 taxonomy 내용 해시가 다릅니다.")
    categories = data.get("all_category_counts")
    _require(isinstance(categories, list) and len(categories) == 247, "업종 집계 행이 247개가 아닙니다.")
    taxonomy_by_code = {row["code"]: row for row in taxonomy.get("categories", [])}
    expected_codes = set(taxonomy_by_code)
    actual_codes = {row.get("code") for row in categories}
    _require(len(actual_codes) == 247 and actual_codes == expected_codes, "업종 코드가 taxonomy와 일치하지 않습니다.")
    _require(all(row.get("abc_tag") in {"A", "B", "C"} for row in categories), "미분류 업종이 있습니다.")
    for row in categories:
        category = taxonomy_by_code[row["code"]]
        _require(row.get("name") == category.get("name"), f"{row['code']} 업종명이 현행 taxonomy와 다릅니다.")
        _require(row.get("abc_tag") == category.get("abc_tag"), f"{row['code']} ABC 태그가 현행 taxonomy와 다릅니다.")

    coverage = data.get("coverage") or {}
    _require(coverage.get("category_count") == 247, "coverage의 업종 수가 247이 아닙니다.")
    a_rows = [row for row in categories if row["abc_tag"] == "A"]
    _require(coverage.get("a_category_count") == len(a_rows), "A 업종 수가 일치하지 않습니다.")
    _require(coverage.get("unclassified_category_count") == 0, "미분류 업종이 있습니다.")

    for item in categories:
        counts = item.get("counts") or {}
        _require(all(radius in counts for radius in RADII), f"{item['code']}의 반경 수가 누락됐습니다.")
        values = [int(counts[radius]) for radius in RADII]
        _require(all(value >= 0 for value in values), f"{item['code']}의 점포 수가 음수입니다.")
        _require(values == sorted(values), f"{item['code']}의 누적 점포 수가 역전됐습니다.")

    overall = sorted(categories, key=rank_key)
    ranked_a = sorted(a_rows, key=rank_key)
    _require(len(data.get("overall_top20", [])) == 20, "전체 TOP20 행 수가 20이 아닙니다.")
    _require(len(data.get("a_top10", [])) == 10, "A TOP10 행 수가 10이 아닙니다.")
    _require([r["code"] for r in overall[:20]] == [r["code"] for r in data["overall_top20"]], "전체 TOP20이 재계산 결과와 다릅니다.")
    _require([r["code"] for r in ranked_a[:10]] == [r["code"] for r in data["a_top10"]], "A TOP10이 재계산 결과와 다릅니다.")
    by_code = {row["code"]: row for row in categories}
    for rows, label in ((data["overall_top20"], "전체 TOP20"), (data["a_top10"], "A TOP10")):
        for rank, row in enumerate(rows, start=1):
            source_row = by_code[row["code"]]
            _require(row.get("rank") == rank, f"{label} 순위 번호가 올바르지 않습니다.")
            _require(row.get("name") == source_row["name"], f"{label} 업종명이 전체 집계와 다릅니다.")
            _require(row.get("abc_tag") == source_row["abc_tag"], f"{label} ABC 태그가 전체 집계와 다릅니다.")
            _require(row.get("counts") == source_row["counts"], f"{label} 점포 수가 전체 집계와 다릅니다.")
    total_5km = sum(int(row["counts"]["5km"]) for row in categories)
    _require(total_5km == int((data.get("coverage") or {}).get("stores_within_5km", -1)), "5km 점포 합계가 coverage와 다릅니다.")
    return {"categories": categories, "overall": overall, "ranked_a": ranked_a, "total_5km": total_5km, "taxonomy_hash": taxonomy_hash}


def _sample_mode(data: dict[str, Any]) -> bool:
    return bool(data.get("sample_redaction"))


def _display_address(data: dict[str, Any]) -> str:
    if _sample_mode(data):
        return "주소 비식별 처리 샘플"
    address = data.get("normalized_address") or data.get("address")
    _require(isinstance(address, str) and address.strip(), "고객 결과에는 분석 주소가 있어야 합니다.")
    return address.strip()


def _kst(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def _safe_request_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", value)[:48]
    return safe or "analysis"


def build_pdf(data: dict[str, Any], validated: dict[str, Any], output: Path) -> None:
    _require(FONT_PATH.exists(), f"한글 글꼴을 찾을 수 없습니다: {FONT_PATH}")
    if "GapKorean" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("GapKorean", str(FONT_PATH)))
    sample = _sample_mode(data)
    address = _display_address(data)
    generated = _kst(data["generated_at"])
    source = data["source"]
    total_5km = validated["total_5km"]
    page_width, _ = landscape(A4)
    usable = page_width - 64
    navy, blue = colors.HexColor(NAVY), colors.HexColor(BLUE)
    ink, muted = colors.HexColor(INK), colors.HexColor(MUTED)
    grid, pale = colors.HexColor(GRID), colors.HexColor(PALE)

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("GapTitle", parent=base["Title"], fontName="GapKorean", fontSize=19, leading=25, textColor=navy, alignment=TA_LEFT, spaceAfter=4),
        "sub": ParagraphStyle("GapSub", parent=base["Normal"], fontName="GapKorean", fontSize=8.5, leading=12, textColor=muted),
        "head": ParagraphStyle("GapHead", parent=base["Heading2"], fontName="GapKorean", fontSize=12, leading=16, textColor=navy, spaceBefore=8, spaceAfter=6),
        "body": ParagraphStyle("GapBody", parent=base["BodyText"], fontName="GapKorean", fontSize=8, leading=11, textColor=ink, spaceAfter=4),
        "small": ParagraphStyle("GapSmall", parent=base["BodyText"], fontName="GapKorean", fontSize=7, leading=9, textColor=muted),
        "cell": ParagraphStyle("GapCell", parent=base["BodyText"], fontName="GapKorean", fontSize=7.1, leading=8.5, textColor=ink),
        "center": ParagraphStyle("GapCenter", parent=base["BodyText"], fontName="GapKorean", fontSize=7.1, leading=8.5, textColor=ink, alignment=TA_CENTER),
        "header": ParagraphStyle("GapHeader", parent=base["BodyText"], fontName="GapKorean", fontSize=7, leading=8.5, textColor=colors.white, alignment=TA_CENTER),
        "notice": ParagraphStyle("GapNotice", parent=base["BodyText"], fontName="GapKorean", fontSize=7.8, leading=11, textColor=colors.HexColor(ORANGE)),
    }

    def p(value: object, style: str = "cell") -> Paragraph:
        return Paragraph(str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), styles[style])

    headers = ["순위", "업종 코드", "세부업종", "태그", *RADII]
    widths = [34, 58, 280, 44, *([67] * 5)]
    overflow = sum(widths) - usable
    if overflow > 0:
        widths[2] -= overflow

    def ranked_table(rows: list[dict[str, Any]]) -> Table:
        values = [[p(value, "header") for value in headers]]
        values.extend([[p(row["rank"], "center"), p(row["code"], "center"), p(row["name"]), p(row["abc_tag"], "center"), *[p(int(row["counts"][radius]), "center") for radius in RADII]] for row in rows])
        table = Table(values, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, pale]), ("LINEBELOW", (0, 0), (-1, -1), 0.25, grid),
        ]))
        return table

    metric = Table([
        [p("분석 기준 주소"), p("대상 업종"), p("A 업종"), p("5km 점포 수"), p("자료 기준일")],
        [p(address), p("247개"), p(f"{len(validated['ranked_a'])}개"), p(f"{total_5km:,}개"), p(source["source_as_of"])],
    ], colWidths=[usable * .34, usable * .13, usable * .13, usable * .18, usable * .22])
    metric.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), blue), ("FONTNAME", (0, 0), (-1, -1), "GapKorean"),
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), .5, grid), ("INNERGRID", (0, 0), (-1, -1), .25, grid),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    story = [
        p("공백상권 분석 결과", "title"),
        p("주소 주변 247개 업종의 공공데이터상 점포 수와 희소 분포를 정리했습니다.", "sub"),
        Spacer(1, 9), metric, Spacer(1, 12),
        p("전체 희소 업종 TOP20", "head"),
        p("5km 점포 수가 적은 순으로 정렬하고, 동률이면 4km부터 1km까지 비교했습니다. 추천 순위가 아니라 관측 공급량 순위입니다.", "small"),
        Spacer(1, 5), ranked_table(data["overall_top20"]), PageBreak(),
        p("생활·방문형(A) 희소 업종 TOP10", "title"),
        p("분석 결과를 확인하기 전에 고정한 A 업종 모집단에서 같은 희소 정렬 기준을 적용했습니다.", "sub"),
        Spacer(1, 11), ranked_table(data["a_top10"]), Spacer(1, 14),
        p("분석 기준과 확인 사항", "head"),
        p("거리 기준은 분석 중심 좌표와 점포 좌표 사이의 직선거리이며, 반경 경계를 포함합니다. 표의 1~5km는 누적 반경입니다.", "body"),
        p(f"자료: 소상공인시장진흥공단 상가(상권)정보 · 기준일 {source['source_as_of']} · snapshot {source['snapshot_id']}", "small"),
        p(f"분류표 {source['taxonomy_version']} · 분석기 {source['analysis_engine_version']}", "small"),
        p(f"taxonomy SHA-256: {validated['taxonomy_hash']}", "small"),
        Spacer(1, 8),
        p("‘공백·희소’는 표기된 기준일의 공공데이터에서 관측된 점포 수가 0개 또는 적다는 뜻입니다.", "notice"),
        p("이는 수요·매출·수익성·창업 성공을 보장하지 않습니다.", "notice"),
        p("데이터는 실시간 현황과 다를 수 있으므로 실제 점포 현황을 확인한 뒤 판단해 주세요.", "notice"),
        p("A/B/C는 업종을 읽기 쉽게 돕는 설명용 태그이며, 창업 적합도나 유망도 점수가 아닙니다.", "small"),
    ]
    output.parent.mkdir(parents=True, exist_ok=True)

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("GapKorean", 7)
        canvas.setFillColor(muted)
        suffix = " · 주소 비식별 샘플" if sample else ""
        canvas.drawString(32, 18, f"자료 기준일 {source['source_as_of']} · 생성 {generated}{suffix}")
        canvas.drawRightString(page_width - 32, 18, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(str(output), pagesize=landscape(A4), rightMargin=32, leftMargin=32, topMargin=30, bottomMargin=32, title="공백상권 분석 결과", author="공백상권 분석")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def build_xlsx(data: dict[str, Any], validated: dict[str, Any], output: Path) -> None:
    sample = _sample_mode(data)
    source = data["source"]
    address = _display_address(data)
    generated = _kst(data["generated_at"])
    all_rows = validated["overall"]
    a_rank = {row["code"]: rank for rank, row in enumerate(validated["ranked_a"], start=1)}
    wb = Workbook()
    summary = wb.active
    summary.title = "요약"
    detail = wb.create_sheet("전체 247개")
    summary.sheet_view.showGridLines = False
    detail.sheet_view.showGridLines = False
    summary.sheet_properties.tabColor = NAVY.lstrip("#")
    detail.sheet_properties.tabColor = "6B7C93"
    summary.append(["공백상권 분석 결과 요약"])
    summary.append(["주소 비식별 처리 샘플" if sample else "고객 납품용 분석 결과"])
    summary.append(["분석 기준 주소", address, "분석 시각 (KST)", generated])
    summary.append(["자료 기준일", source["source_as_of"], "대상 업종", 247])
    summary.append(["분류표 버전", source["taxonomy_version"]])
    summary.append(["5km 점포 합계", validated["total_5km"], "기준", "누적 반경 1~5km"])
    summary.append(["자료 출처", source["source_url"]])
    summary.append(["taxonomy SHA-256", validated["taxonomy_hash"]])
    summary.append(["전체 희소 업종 TOP20"])
    summary.append(["순위", "업종 코드", "세부업종", "태그", *[f"{r} (개)" for r in RADII]])
    for row in data["overall_top20"]:
        summary.append([row["rank"], row["code"], row["name"], row["abc_tag"], *[int(row["counts"][r]) for r in RADII]])
    summary.append([])
    summary.append(["생활·방문형(A) 희소 업종 TOP10"])
    summary.append(["순위", "업종 코드", "세부업종", "태그", *[f"{r} (개)" for r in RADII]])
    for row in data["a_top10"]:
        summary.append([row["rank"], row["code"], row["name"], row["abc_tag"], *[int(row["counts"][r]) for r in RADII]])
    summary.append([])
    summary.append(["분석 기준", "직선거리 기준, 반경 경계 포함. 표의 1~5km는 누적 반경입니다."])
    summary.append(["희소 정렬", "5km→4km→3km→2km→1km 점포 수 오름차순, 동률이면 업종 코드 오름차순."])
    summary.append(["태그", "A 생활·방문형 / B 시설·입지 의존형 / C 특수·전문 목적형. 설명용 태그입니다."])
    summary.append(["주의", "공백·희소는 공공데이터 기준일의 관측 점포 수이며, 수요·수익성·창업 성공을 뜻하지 않습니다."])

    detail.append(["전체 247개 업종 점포 수와 희소 순위"])
    detail.append(["주소 비식별 처리 샘플" if sample else "고객 납품용 분석 결과", "자료 기준일", source["source_as_of"], "분석 시각 (KST)", generated])
    detail.append(["전체 순위는 5km→4km→3km→2km→1km 오름차순. A 순위는 고정된 A 모집단 안에서 산출. 점포 수는 누적값."])
    headers = ["전체 희소 순위", "A형 내 순위", "업종 코드", "세부업종", "태그", *[f"{r} (개)" for r in RADII]]
    detail.append(headers)
    for rank, row in enumerate(all_rows, start=1):
        detail.append([rank, a_rank.get(row["code"]), row["code"], row["name"], row["abc_tag"], *[int(row["counts"][r]) for r in RADII]])

    navy_fill = PatternFill("solid", fgColor=NAVY.lstrip("#"))
    blue_fill = PatternFill("solid", fgColor=BLUE.lstrip("#"))
    pale_fill = PatternFill("solid", fgColor=PALE.lstrip("#"))
    white_bold = Font(name="Arial", size=9, bold=True, color="FFFFFF")
    thin_gray = Side(style="thin", color=GRID.lstrip("#"))
    for sheet in (summary, detail):
        sheet.sheet_view.showGridLines = False
        sheet.freeze_panes = "A10" if sheet is summary else "A5"
    summary.merge_cells("A1:I1")
    summary["A1"].font = Font(name="Arial", size=16, bold=True, color=NAVY.lstrip("#"))
    summary.merge_cells("A2:I2")
    summary["A2"].font = Font(name="Arial", size=10, bold=True, color="9A3412")
    summary.merge_cells("B8:I8")
    summary["A8"].font = Font(name="Arial", size=8, bold=True, color=MUTED.lstrip("#"))
    summary["B8"].font = Font(name="Arial", size=8, color=INK.lstrip("#"))
    summary["B8"].alignment = Alignment(vertical="center")
    summary["A1"].alignment = Alignment(vertical="center")
    for row_num in (9, 10, 32, 33):
        if row_num in (10, 33):
            for cell in summary[row_num][:9]:
                cell.fill = navy_fill
                cell.font = white_bold
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        else:
            for cell in summary[row_num]:
                cell.fill = blue_fill
                cell.font = Font(name="Arial", size=11, bold=True, color=NAVY.lstrip("#"))
    for row_num in list(range(11, 31)) + list(range(34, 44)):
        if row_num % 2 == 0:
            for cell in summary[row_num]:
                cell.fill = pale_fill
        for cell in summary[row_num]:
            cell.border = Border(bottom=thin_gray)
        for cell in summary[row_num][4:9]:
            cell.number_format = "#,##0"
            cell.alignment = Alignment(horizontal="center")
    for r in range(1, summary.max_row + 1):
        for c in range(1, summary.max_column + 1):
            cell = summary.cell(r, c)
            cell.alignment = Alignment(horizontal=cell.alignment.horizontal, vertical="center", wrap_text=True)
    for c, width in enumerate([18, 17, 34, 13, 14, 14, 14, 14, 14], start=1):
        summary.column_dimensions[get_column_letter(c)].width = width
    summary.column_dimensions["B"].width = 42
    summary["B6"].number_format = "#,##0"
    summary.row_dimensions[1].height = 25
    summary.row_dimensions[8].height = 20
    summary.auto_filter.ref = "A10:I30"
    summary.sheet_properties.pageSetUpPr.fitToPage = True
    summary.page_setup.orientation = "landscape"
    summary.page_setup.fitToWidth = 1
    summary.page_setup.fitToHeight = 0
    summary.print_title_rows = "1:10"

    detail.merge_cells("A1:J1")
    detail.merge_cells("A2:B2")
    detail.merge_cells("A3:J3")
    detail["A1"].font = Font(name="Arial", size=15, bold=True, color=NAVY.lstrip("#"))
    detail["A2"].font = Font(name="Arial", size=9, color=MUTED.lstrip("#"))
    detail["A3"].font = Font(name="Arial", size=9, color=MUTED.lstrip("#"))
    for cell in detail[4]:
        cell.fill = navy_fill
        cell.font = white_bold
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row_num in range(5, 252):
        if row_num % 2 == 0:
            for cell in detail[row_num]:
                cell.fill = pale_fill
        for cell in detail[row_num]:
            cell.border = Border(bottom=thin_gray)
            cell.alignment = Alignment(vertical="center")
        for cell in detail[row_num][0:2] + detail[row_num][4:10]:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for cell in detail[row_num][5:10]:
            cell.number_format = "#,##0"
    for c, width in enumerate([18, 15, 16, 38, 12, 13, 13, 13, 13, 13], start=1):
        detail.column_dimensions[get_column_letter(c)].width = width
    detail.row_dimensions[4].height = 30
    detail.auto_filter.ref = "A4:J251"
    detail.sheet_properties.pageSetUpPr.fitToPage = True
    detail.page_setup.orientation = "landscape"
    detail.page_setup.fitToWidth = 1
    detail.page_setup.fitToHeight = 0
    detail.print_title_rows = "1:4"
    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)
    saved = load_workbook(output, read_only=True, data_only=True)
    try:
        ws = saved["전체 247개"]
        _require(ws.max_row == 251, "저장된 XLSX에 247개 전체 행이 없습니다.")
        _require(tuple(ws.cell(4, c).value for c in range(1, 11)) == tuple(headers), "저장된 XLSX의 열 구성이 다릅니다.")
    finally:
        saved.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="analyzer result JSON")
    parser.add_argument("--output-dir", type=Path, help="destination within this project; defaults to output/deliveries/<request_id>")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    validated = validate_result(data, taxonomy)
    request_id = _safe_request_id(str(data.get("request_id") or "analysis"))
    out_dir = args.output_dir.resolve() if args.output_dir else (ROOT / "output" / "deliveries" / request_id).resolve()
    _require(out_dir.is_relative_to(ROOT), "납품물 저장 폴더는 프로젝트 폴더 안이어야 합니다.")
    suffix = "_샘플" if _sample_mode(data) else ""
    pdf = out_dir / f"공백상권_{request_id}_요약{suffix}.pdf"
    xlsx = out_dir / f"공백상권_{request_id}_247업종{suffix}.xlsx"
    build_pdf(data, validated, pdf)
    build_xlsx(data, validated, xlsx)
    result = {
        "status": "validated_and_written",
        "sample_only": _sample_mode(data),
        "output_files": [
            {"path": str(pdf.relative_to(ROOT)), "bytes": pdf.stat().st_size},
            {"path": str(xlsx.relative_to(ROOT)), "bytes": xlsx.stat().st_size},
        ],
        "category_rows": len(validated["categories"]),
        "overall_top20_rows": len(data["overall_top20"]),
        "a_top10_rows": len(data["a_top10"]),
        "total_5km": validated["total_5km"],
        "address_included": not _sample_mode(data),
        "publication_gate": data["publication_gate"],
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
