#!/usr/bin/env python3
"""Build a masked, clearly non-publishable HTML preview from a sample result."""

from __future__ import annotations

import html
import json
from pathlib import Path

try:
    from .taxonomy_integrity import verify_taxonomy
except ImportError:  # Running this file directly from the scripts directory.
    from taxonomy_integrity import verify_taxonomy


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "results" / "sample_workflow_address_taxonomy_v3_20260930.json"
OUTPUT = ROOT / "판매자료" / "샘플_공백상권_결과_검토용.html"
TAXONOMY = ROOT / "설계" / "03_업종분류표.json"
DATABASE = ROOT / "data" / "processed" / "semas_store_20260630.sqlite"
SAMPLE_DATABASE_META = ROOT / "data" / "results" / "sample_database_meta.json"
RADII = ("1km", "2km", "3km", "4km", "5km")


def cell(value: object) -> str:
    return html.escape(str(value), quote=True)


def table(rows: list[dict]) -> str:
    body = []
    for row in rows:
        counts = row["counts"]
        tag = row.get("abc_tag", "미분류")
        body.append(
            "<tr>"
            f"<td class='rank'>{cell(row['rank'])}</td>"
            f"<td><strong>{cell(row['name'])}</strong><small>{cell(row['code'])}</small></td>"
            f"<td><span class='tag tag-{cell(tag)}'>{cell(tag)}</span></td>"
            + "".join(f"<td class='num'>{cell(counts[radius])}</td>" for radius in RADII)
            + "</tr>"
        )
    return (
        "<div class='table-wrap'><table><thead><tr>"
        "<th>순위</th><th>세부업종</th><th>태그*</th>"
        + "".join(f"<th>{radius}</th>" for radius in RADII)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def main() -> int:
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    verify_taxonomy(taxonomy)
    db_meta: dict[str, str] = {}
    if DATABASE.exists():
        import sqlite3

        with sqlite3.connect(f"file:{DATABASE.as_posix()}?mode=ro", uri=True) as connection:
            db_meta = dict(connection.execute("SELECT key, value FROM meta"))
    elif SAMPLE_DATABASE_META.is_file():
        db_meta = json.loads(SAMPLE_DATABASE_META.read_text(encoding="utf-8"))
    counts = data["all_category_counts"]
    totals = {radius: sum(item["counts"][radius] for item in counts) for radius in RADII}
    source = data["source"]
    gate = html.escape(data.get("publication_gate", "unknown"))
    result_taxonomy = source.get("taxonomy_version", "unknown")
    current_taxonomy = taxonomy.get("taxonomy_version", "unknown")
    database_taxonomy = db_meta.get("taxonomy_version", "unknown")
    result_hash = source.get("taxonomy_hash", "unknown")
    current_hash = taxonomy.get("taxonomy_hash", "unknown")
    database_hash = db_meta.get("taxonomy_hash", "unknown")
    taxonomy_stale = (
        result_taxonomy != current_taxonomy
        or database_taxonomy != current_taxonomy
        or result_hash != current_hash
        or database_hash != current_hash
    )
    tag_counts = taxonomy.get("abc_counts", {})
    tag_summary = " / ".join(
        f"{cell(tag)} {cell(tag_counts.get(tag, 0))}개"
        for tag in ("A", "B", "C", "미분류")
    )

    summary_cards = "".join(
        f"<div class='metric'><span>{radius} 내 관측 점포</span><strong>{totals[radius]:,}</strong></div>"
        for radius in RADII
    )
    stale_notice = ""
    if taxonomy_stale:
        stale_notice = (
            "<div class='alert alert-stale'><strong>이 표본은 현재 분류표 기준의 결과가 아닙니다.</strong><br>"
            f"표본 결과: {cell(result_taxonomy)} · 현재 검토 분류표: {cell(current_taxonomy)} · "
            f"현재 분석 DB: {cell(database_taxonomy)}<br>"
            f"표본 해시: {cell(result_hash)} · 현재 해시: {cell(current_hash)} · DB 해시: {cell(database_hash)}<br>"
            f"현재 태그 검토 현황: {tag_summary}. 구버전 A/B/C 태그와 A TOP10은 현행 결과로 사용하지 마세요. "
            "분류표 최종 승인 후 분석 DB와 표본을 다시 생성해야 합니다.</div>"
        )
    html_doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>공백상권 분석 검토용 샘플</title>
<style>
:root{{--navy:#15263e;--blue:#3768d7;--ink:#24344b;--muted:#68778b;--line:#dce4ee;--paper:#f4f7fb;--warn:#fff2d5;--warn-ink:#815500}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Arial,"Malgun Gothic",sans-serif;line-height:1.5}}
.page{{max-width:1100px;margin:28px auto;padding:32px;background:white;box-shadow:0 8px 30px #18305214;position:relative;overflow:hidden}}
.watermark{{position:absolute;top:17px;right:-39px;transform:rotate(34deg);background:#a82626;color:#fff;padding:7px 48px;font-size:12px;font-weight:800;letter-spacing:.08em}}
.eyebrow{{color:var(--blue);font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}}
h1{{font-size:32px;line-height:1.25;color:var(--navy);margin:8px 0 6px}}.sub{{color:var(--muted);margin:0 0 22px}}
.alert{{padding:14px 16px;background:var(--warn);border:1px solid #f3d793;color:var(--warn-ink);border-radius:10px;font-size:14px;font-weight:700;margin:22px 0}}
.alert-stale{{background:#ffe5e5;border-color:#e9a0a0;color:#8c2020}}
.meta{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:20px 0}}.meta div,.metric{{padding:13px 15px;border:1px solid var(--line);border-radius:10px;background:#fff}}
.meta span,.metric span{{display:block;color:var(--muted);font-size:12px}}.meta strong{{font-size:14px}}
.metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin:16px 0 28px}}.metric strong{{display:block;color:var(--navy);font-size:23px;margin-top:4px}}
h2{{font-size:20px;color:var(--navy);margin:30px 0 12px}}.intro{{font-size:14px;color:var(--muted);margin:-4px 0 12px}}
.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:13px}}th{{background:var(--navy);color:#fff;padding:9px 8px;text-align:center;white-space:nowrap}}td{{border-bottom:1px solid var(--line);padding:8px;text-align:center}}td:nth-child(2){{text-align:left;min-width:220px}}td small{{display:block;color:var(--muted);font-size:11px;margin-top:2px}}.rank{{font-weight:800;color:var(--blue)}}.num{{font-variant-numeric:tabular-nums}}
.tag{{display:inline-block;min-width:25px;border-radius:12px;padding:2px 7px;font-size:11px;font-weight:800;background:#edf1f6;color:#59677a}}.tag-A{{background:#e6f4ec;color:#167044}}.tag-B{{background:#e9efff;color:#3156a5}}.tag-C{{background:#f1eafb;color:#7045a6}}
.note{{font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px;margin-top:26px}}.source{{font-size:11px;color:var(--muted);margin-top:18px}}
@media(max-width:700px){{.page{{margin:0;padding:22px 16px}}h1{{font-size:26px}}.meta{{grid-template-columns:1fr 1fr}}.metrics{{grid-template-columns:repeat(2,1fr)}}.metrics .metric:last-child{{grid-column:span 2}}}}
@media print{{body{{background:#fff}}.page{{max-width:none;margin:0;padding:12mm;box-shadow:none}}.table-wrap{{overflow:visible}}table{{font-size:10px}}th,td{{padding:5px}}h2{{break-after:avoid}}tr{{break-inside:avoid}}.watermark{{position:fixed}}}}
</style></head><body><main class="page">
<div class="watermark">검토용 · 외부공개 금지</div>
<div class="eyebrow">LOCAL STORE SUPPLY SNAPSHOT</div>
<h1>공백상권 분석</h1><p class="sub">247개 업종 중 주변에서 적게 관측된 업종을 거리별 점포 수로 확인합니다.</p>
<div class="alert">내부 검토용 주소 마스킹 샘플입니다. 분석 발행 게이트: {gate}. 실제 고객 납품 전에는 주문 주소·실행기록과 결과 수치를 별도로 확인해야 합니다.</div>
{stale_notice}
<section class="meta"><div><span>분석 예시 위치</span><strong>부산광역시 연제구 · 상세주소 비공개</strong></div><div><span>원자료 기준일</span><strong>{cell(source['source_as_of'])}</strong></div><div><span>검토 상태</span><strong>내부 표본 · 분석 발행 게이트 통과</strong></div></section>
<div class="metrics">{summary_cards}</div>
<h2>전체 희소 업종 TOP20</h2><p class="intro">247개 업종 전체에서 5km 수가 적은 순으로 정렬했습니다. 동률이면 4km부터 1km까지 차례로 비교합니다.</p>
{table(data['overall_top20'])}
<h2>생활·방문형(A) 희소 업종 TOP10</h2><p class="intro">결과를 확인하기 전에 고정한 A 업종 모집단 안에서 동일한 희소 정렬 기준을 적용했습니다.</p>
{table(data['a_top10'])}
<div class="note"><strong>*A/B/C 태그 안내</strong><br>A/B/C는 업종 성격을 읽기 쉽게 돕기 위한 내부 참고 태그입니다. 공식 업종 분류가 아니며 창업 적합성·수요·수익성을 뜻하지 않습니다. 본 분석의 ‘공백·희소’는 해당 기준일의 공공데이터에서 관측된 점포 수를 말합니다. 공백이 수요나 수익성을 의미하지 않으며, 실제 점포 현황은 최종 의사결정 전에 직접 확인해야 합니다.</div>
<div class="source">자료: 소상공인시장진흥공단 상가(상권)정보 · snapshot {cell(source['snapshot_id'])} · 분류표 {cell(source['taxonomy_version'])} · 분석기 {cell(source['analysis_engine_version'])}<br>검토용 표본 결과 · 5km 이내 {totals['5km']:,}개 점포 레코드 · 거리 기준은 좌표 간 직선거리, 반경 경계 포함</div>
</main></body></html>
"""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html_doc, encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT.relative_to(ROOT)), "status": "review_only", "address_masked": True}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
