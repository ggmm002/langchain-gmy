from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    KNOWLEDGE_RAG = "knowledge_rag"
    SUPPLY_ANALYTICS = "supply_analytics"
    WORKFLOW_AUTOMATION = "workflow_automation"
    CLARIFICATION = "clarification"


class RouteTarget(str, Enum):
    KNOWLEDGE_RAG = "knowledge_rag"
    SUPPLY_ANALYTICS = "supply_analytics"
    WORKFLOW_AUTOMATION = "workflow_automation"
    CLARIFICATION = "clarification"


class ExtractedEntities(BaseModel):
    suppliers: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    time_range: str | None = None
    workflow_name: str | None = None
    knowledge_topics: list[str] = Field(default_factory=list)
    action_verb: str | None = None


class IntentDecision(BaseModel):
    source: str
    primary_intent: IntentType
    secondary_intents: list[IntentType] = Field(default_factory=list)
    route_target: RouteTarget
    confidence: float = Field(ge=0.0, le=1.0)
    requires_confirmation: bool = False
    missing_slots: list[str] = Field(default_factory=list)
    normalized_query: str
    rationale: str
    extracted_entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    risk_flags: list[str] = Field(default_factory=list)
    candidate_scores: dict[str, float] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class TraceEvent(BaseModel):
    stage: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SubsystemResult(BaseModel):
    target: RouteTarget
    status: str
    title: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    allow_action_execution: bool = False
    dry_run: bool = True
    trace: bool = True


class HealthResponse(BaseModel):
    status: str
    app_name: str
    version: str
    llm_configured: bool
    llm_enabled: bool
    llm_status: str


class AnalyzeResponse(BaseModel):
    request_id: str
    query: str
    classification: IntentDecision
    rule_decision: IntentDecision
    llm_decision: IntentDecision | None = None
    subsystem_result: SubsystemResult
    trace: list[TraceEvent] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
