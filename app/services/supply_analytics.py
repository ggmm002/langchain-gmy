from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.models import ExtractedEntities, RouteTarget, SubsystemResult


class SupplyAnalyticsService:
    def __init__(self, data_dir: Path) -> None:
        self._records = self._load_records(data_dir / "supply_records.json")
        self._anchor_date = max(date.fromisoformat(record["date"]) for record in self._records)

    @staticmethod
    def _load_records(path: Path) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            raise ValueError("supply_records.json 必须是数组。")

        return data

    def get_suppliers(self) -> list[str]:
        return sorted({str(record["supplier"]) for record in self._records})

    def get_materials(self) -> list[str]:
        return sorted({str(record["material"]) for record in self._records})

    def run(self, query: str, entities: ExtractedEntities) -> SubsystemResult:
        filtered_records = self._filter_records(entities)
        if not filtered_records:
            return SubsystemResult(
                target=RouteTarget.SUPPLY_ANALYTICS,
                status="no_data",
                title="没有匹配到供应量数据",
                summary="当前筛选条件下没有找到可用于分析的供应量记录，请补充时间范围、供应商或物料。",
                data={"records": []},
                suggestions=[
                    "增加时间范围，例如“近三个月”或“2026年3月”",
                    "增加供应商名称，例如“华东智造”",
                    "增加物料名称，例如“动力电池模组”",
                ],
            )

        total_planned = sum(float(record["planned_volume"]) for record in filtered_records)
        total_actual = sum(float(record["actual_volume"]) for record in filtered_records)
        total_delivered = sum(float(record["delivered_volume"]) for record in filtered_records)
        completion_rate = round(total_actual / total_planned, 4) if total_planned else 0.0
        delivery_rate = round(total_delivered / total_planned, 4) if total_planned else 0.0
        gap = round(total_planned - total_actual, 2)

        abnormal_rows = [
            record
            for record in filtered_records
            if float(record["actual_volume"]) < float(record["planned_volume"]) * 0.9
        ]
        monthly_breakdown = self._aggregate_by_month(filtered_records)
        top_gap_records = sorted(
            filtered_records,
            key=lambda record: float(record["planned_volume"]) - float(record["actual_volume"]),
            reverse=True,
        )[:3]

        filters = {
            "suppliers": entities.suppliers,
            "materials": entities.materials,
            "time_range": entities.time_range or "未指定，默认使用全部样本",
        }
        summary = (
            f"已分析 {len(filtered_records)} 条供应量记录，计划 {total_planned:.0f}、实际 {total_actual:.0f}、"
            f"达成率 {completion_rate:.1%}、交付率 {delivery_rate:.1%}。"
        )

        if abnormal_rows:
            summary += f" 当前共有 {len(abnormal_rows)} 条低于 90% 达成率的异常记录。"

        return SubsystemResult(
            target=RouteTarget.SUPPLY_ANALYTICS,
            status="success",
            title="供应量数据分析报告",
            summary=summary,
            data={
                "query": query,
                "filters": filters,
                "overview": {
                    "planned_volume": round(total_planned, 2),
                    "actual_volume": round(total_actual, 2),
                    "delivered_volume": round(total_delivered, 2),
                    "gap_volume": gap,
                    "completion_rate": completion_rate,
                    "delivery_rate": delivery_rate,
                },
                "monthly_breakdown": monthly_breakdown,
                "top_gap_records": top_gap_records,
                "abnormal_records": abnormal_rows,
            },
            suggestions=[
                "如需周报格式，可继续追问“请整理成周会汇报摘要”",
                "如需联动处置，可继续追问“基于异常记录触发补货流程”",
            ],
        )

    def _filter_records(self, entities: ExtractedEntities) -> list[dict[str, Any]]:
        results = list(self._records)

        if entities.suppliers:
            supplier_set = set(entities.suppliers)
            results = [record for record in results if record["supplier"] in supplier_set]

        if entities.materials:
            material_set = set(entities.materials)
            results = [record for record in results if record["material"] in material_set]

        if entities.time_range:
            start_date, end_date = self._resolve_time_range(
                entities.time_range, reference_date=self._anchor_date
            )
            if start_date and end_date:
                results = [
                    record
                    for record in results
                    if start_date <= date.fromisoformat(record["date"]) <= end_date
                ]

        return results

    @staticmethod
    def _resolve_time_range(
        raw_value: str,
        reference_date: date,
    ) -> tuple[date | None, date | None]:
        value = raw_value.strip()

        if value in {"近7天", "最近7天"}:
            return reference_date - timedelta(days=7), reference_date
        if value in {"近30天", "最近30天"}:
            return reference_date - timedelta(days=30), reference_date
        if value in {"近三个月", "最近三个月"}:
            return reference_date - timedelta(days=90), reference_date
        if value == "本月":
            start = reference_date.replace(day=1)
            return start, reference_date
        if value == "上月":
            first_day = reference_date.replace(day=1)
            last_month_end = first_day - timedelta(days=1)
            return last_month_end.replace(day=1), last_month_end

        normalized = value.replace("年", "-").replace("月", "").replace("/", "-")
        if len(normalized) == 7 and normalized.count("-") == 1:
            year, month = normalized.split("-")
            start = date(int(year), int(month), 1)
            if int(month) == 12:
                end = date(int(year) + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(int(year), int(month) + 1, 1) - timedelta(days=1)
            return start, end

        return None, None

    @staticmethod
    def _aggregate_by_month(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        monthly_totals: dict[str, dict[str, float]] = defaultdict(
            lambda: {"planned_volume": 0.0, "actual_volume": 0.0, "delivered_volume": 0.0}
        )

        for record in records:
            month = str(record["date"])[:7]
            monthly_totals[month]["planned_volume"] += float(record["planned_volume"])
            monthly_totals[month]["actual_volume"] += float(record["actual_volume"])
            monthly_totals[month]["delivered_volume"] += float(record["delivered_volume"])

        monthly_breakdown: list[dict[str, Any]] = []
        for month, values in sorted(monthly_totals.items()):
            planned_volume = values["planned_volume"]
            actual_volume = values["actual_volume"]
            monthly_breakdown.append(
                {
                    "month": month,
                    "planned_volume": round(planned_volume, 2),
                    "actual_volume": round(actual_volume, 2),
                    "delivered_volume": round(values["delivered_volume"], 2),
                    "completion_rate": round(actual_volume / planned_volume, 4)
                    if planned_volume
                    else 0.0,
                }
            )

        return monthly_breakdown
