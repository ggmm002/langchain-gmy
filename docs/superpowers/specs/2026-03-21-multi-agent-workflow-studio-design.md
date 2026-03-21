# Multi-Agent Workflow Studio Design

## Overview

This document defines the first production-leaning prototype for a LangChain + LangGraph multi-agent workflow studio. The product goal is to let a user submit a complex task through a chat-first interface, watch a LangGraph workflow execute in real time, inspect node-level inputs and outputs, and switch into a graph-focused workflow view similar in spirit to Dify's workflow runtime.

The first version intentionally optimizes for a strong local product prototype rather than a full workflow platform. It will support a fixed workflow template with lightweight runtime configuration, real-time node execution monitoring, and architecture boundaries that allow persistence to be added later without replacing the graph runtime or the frontend experience.

## Goals

- Build a chat-driven workflow studio for complex task execution.
- Use LangGraph to orchestrate a dynamic multi-agent workflow with conditional routing and bounded loops.
- Show runtime progress clearly in the frontend, including active node, node status, node summaries, timings, and recent events.
- Provide a full workflow graph view in addition to the primary chat view.
- Keep backend boundaries clear enough to swap in SQLite or another persistence layer later.

## Non-Goals

- Free-form visual workflow authoring with arbitrary drag-and-drop nodes.
- Full workflow versioning, publishing, or multi-template management.
- Multi-user authentication, authorization, or tenant isolation.
- External search integrations or a production tool marketplace.
- Production deployment infrastructure.

## Product Scope

### Primary User Experience

The primary experience is a chat-first workflow console:

1. The user enters a complex task in a chat composer.
2. The frontend creates a workflow run with the selected runtime configuration.
3. The backend starts a LangGraph execution.
4. The frontend subscribes to run events and updates three synchronized surfaces:
   - the chat transcript
   - the runtime monitor sidebar
   - the workflow graph view
5. The user can expand into a dedicated workflow canvas view to inspect graph execution more closely.

### Lightweight Configuration

The first version will allow the user to adjust a small set of template parameters before running:

- model name
- temperature
- maximum review loop count
- whether the reviewer agent is enabled
- planning granularity

These settings modify runtime behavior, but do not change the fundamental graph topology.

## Architecture

The system uses a frontend/backend split.

### Frontend

The frontend will use React + Vite plus React Flow and present a polished cockpit-style interface with two coordinated modes:

- **Chat + Monitor view:** the default page, optimized for running tasks and following progress.
- **Workflow Canvas view:** a graph-centric view for node relationships, path highlighting, and execution state.

Frontend responsibilities:

- capture user task input and runtime settings
- create runs through the API
- subscribe to streaming run events
- maintain a normalized client-side run state
- render chat events, node states, timing, and result summaries
- render the workflow graph with execution highlighting

### Backend

The backend will use FastAPI and remain organized into three clear layers:

1. **Workflow Runtime**
   - owns the LangGraph graph definition
   - defines node handlers and routing rules
   - executes the multi-agent workflow

2. **Run State Adapter**
   - transforms raw LangGraph state transitions into frontend-facing run events
   - builds run snapshots for refresh and reconnect scenarios
   - hides storage details behind a repository boundary

3. **Workflow API**
   - exposes run creation, event streaming, snapshot retrieval, and workflow template configuration endpoints
   - validates input payloads
   - coordinates runtime execution and event delivery

## Multi-Agent Graph Design

### Core Workflow

The graph is a fixed template with dynamic execution paths. The first version will include these conceptual nodes:

- `Planner`
- `Router`
- `Researcher`
- `Executor`
- `Reviewer`
- `Finalizer`
- `Failure`

### Execution Flow

1. `Planner` receives the user goal and produces a structured execution plan.
2. `Router` inspects workflow state and chooses the next step.
3. `Researcher` gathers or organizes the context needed for execution.
4. `Executor` produces the current work artifact.
5. `Reviewer` evaluates whether the output meets the completion criteria.
6. `Router` decides whether to:
   - continue to `Finalizer`
   - loop back for another research/execution pass
   - stop at `Failure`

This creates a graph that looks stable in the UI while still expressing the value of LangGraph through conditional branching and bounded iteration.

### Agent Responsibilities

- **Planner:** convert the user goal into a structured plan with tasks, success criteria, and current objectives.
- **Researcher:** build the working context needed by downstream nodes.
- **Executor:** generate the current task result for the active objective.
- **Reviewer:** decide whether the current result is acceptable, and if not, provide revision guidance.
- **Finalizer:** compose the final user-facing answer and run summary.

## State Model

The runtime needs a shared workflow state that can be used by both the graph and the event adapter. The exact implementation can evolve, but the state shape must support:

- run id
- original user goal
- runtime configuration
- planner output
- current task objective
- accumulated research context
- latest execution artifact
- review feedback
- loop counters
- node statuses
- event timeline
- final result
- failure details

The event adapter must be able to derive both:

- an append-only event stream for real-time updates
- a current snapshot for reload and resume-from-view scenarios

## Frontend Design

### Chat + Monitor View

This is the default landing page and the main experience for the first version.

Layout:

- **Top bar:** product identity, run summary, entry to the workflow canvas view
- **Main column:** chat transcript, task input, and final result
- **Right sidebar:** node monitor, active node card, node summaries, timing, and recent runtime events

The chat transcript will include both conversational content and structured workflow progress messages, such as planning completion, routing decisions, and final status.

### Workflow Canvas View

This view emphasizes the graph itself and will open as a dedicated route launched from the default chat view. It must show:

- fixed graph topology
- per-node state badges
- active node highlight
- highlighted traversed path
- a details panel for the selected node

The graph does not need drag-and-drop editing in the first version.

### Visual Direction

The UI should feel like a modern product prototype rather than a plain dashboard template:

- deep navy foundation with teal/cyan execution highlights
- layered cards and atmospheric background treatment
- clear status motion for running nodes
- strong visual relationship between chat events and graph activity

## API Design

The first version will use Server-Sent Events for one-way runtime streaming and should include these endpoints:

- `POST /api/runs`
  - creates a new workflow run
  - accepts user task plus workflow configuration

- `GET /api/runs/:id/stream`
  - streams normalized run events to the frontend
  - supports the live monitor experience

- `GET /api/runs/:id`
  - returns the current run snapshot
  - supports page refresh and late attachment

- `GET /api/workflows/default`
  - returns the default workflow template metadata and graph description

- `PATCH /api/workflows/default/config`
  - updates the active default template configuration

The first version can back these APIs with in-memory storage as long as the storage implementation sits behind a repository interface.

## Event Model

The frontend observability experience depends on a clean event contract. The event adapter should standardize at least these event categories:

- run created
- run started
- node queued
- node started
- node completed
- node failed
- route decided
- review requested
- run completed
- run failed

Each event should include enough metadata for the frontend to update the graph and sidebar without re-deriving workflow intent from raw model output. At minimum, events should support:

- run id
- event type
- node id
- timestamp
- status
- summary text
- optional input summary
- optional output summary
- optional elapsed time

## Persistence Strategy

The first version will not require durable storage, but it must be designed so persistence can be added without structural rework.

The backend should define repository abstractions for:

- workflow template configuration
- run snapshots
- run event history

The first implementation can use in-memory adapters. A future SQLite adapter should be able to implement the same contract.

## Error Handling

The prototype must handle failure clearly rather than hiding it.

Required behaviors:

- invalid user input returns a clear API validation error
- node-level failures emit structured failure events
- the graph stops cleanly when a terminal failure state is reached
- the frontend renders failed nodes distinctly from successful nodes
- the sidebar shows failure reason and last successful node
- the run snapshot preserves terminal failure information

If the reviewer loop exceeds the configured maximum, the graph should end in a controlled `Failure` terminal state that preserves the latest reviewer feedback and most recent execution artifact in the run snapshot.

## Testing Strategy

Testing will cover three levels:

### Backend Unit Tests

- graph routing logic
- loop bound behavior
- agent node output normalization
- run state adapter event conversion
- repository interface behavior for the in-memory implementation

### API Integration Tests

- run creation
- event stream contract
- snapshot retrieval
- configuration updates
- failure propagation to API responses and stream events

### Frontend Tests

- chat submission flow
- stream-driven node status updates
- monitor sidebar rendering
- graph node highlighting
- selected node inspection behavior

## Delivery Boundaries

The first implementation should produce a polished local prototype that is easy to run and easy to extend. It should be treated as a product-quality foundation, not a throwaway demo, while still avoiding platform-level scope such as general workflow authoring and full persistence.

## Technology Decisions Locked For Planning

The implementation plan should assume these concrete choices:

- Server-Sent Events for runtime streaming
- a dedicated workflow canvas route launched from the default chat view
- React Flow for graph rendering
- FastAPI for the Python API layer
- max-loop exhaustion maps to a `Failure` terminal state
