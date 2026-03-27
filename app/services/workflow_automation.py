from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.models import ExtractedEntities, RouteTarget, SubsystemResult


class WorkflowAutomationService:
    def __init__(self, data_dir: Path) -> None:
        self._workflows = self._load_workflows(data_dir / "workflow_catalog.json")

    @staticmethod
    def _load_workflows(path: Path) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            raise ValueError("workflow_catalog.json 必须是数组。")

        return data

    def get_workflow_names(self) -> list[str]:
        return sorted(str(workflow["name"]) for workflow in self._workflows)

    def run(
        self,
        query: str,
        entities: ExtractedEntities,
        allow_action_execution: bool,
        dry_run: bool,
    ) -> SubsystemResult:
        workflow = self._match_workflow(entities.workflow_name or query)
        if workflow is None:
            return SubsystemResult(
                target=RouteTarget.WORKFLOW_AUTOMATION,
                status="clarification_required",
                title="需要明确要触发的工作流",
                summary="当前请求已识别为工作流自动化，但无法确定具体流程名称或执行目标。",
                data={"available_workflows": self.get_workflow_names()},
                suggestions=[
                    "请明确工作流名称，例如“供应异常预警流程”",
                    "如仅需分析数据，请改问“分析近三个月供应量趋势”",
                ],
            )

        execution_mode = "dry_run"
        status = "planned"
        if allow_action_execution and not dry_run:
            execution_mode = "execute"
            status = "accepted"

        audit_id = f"audit-{uuid.uuid4().hex[:10]}"
        steps = workflow.get("steps", [])
        summary = (
            f"已识别工作流 {workflow['name']}，当前以 {execution_mode} 模式生成执行计划。"
            " 生产环境建议接入审批、幂等键和回滚策略后再放开真实执行。"
        )

        return SubsystemResult(
            target=RouteTarget.WORKFLOW_AUTOMATION,
            status=status,
            title="工作流自动化执行计划",
            summary=summary,
            data={
                "query": query,
                "workflow": workflow,
                "execution_mode": execution_mode,
                "audit_id": audit_id,
                "steps": steps,
            },
            suggestions=[
                "若要真实执行，请将 allow_action_execution 置为 true 且关闭 dry_run",
                "建议接入企业审批流后再开放高风险动作",
            ],
        )

    def _match_workflow(self, text: str) -> dict[str, Any] | None:
        normalized = text.strip().lower()

        for workflow in self._workflows:
            name = str(workflow["name"])
            aliases = [name, *workflow.get("aliases", [])]
            if any(alias.lower() in normalized for alias in aliases):
                return workflow

        return None
