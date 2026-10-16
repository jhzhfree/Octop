# Organization and workforce model

## Purpose

The organization model adds a configurable workforce structure around Octop's existing users and Agents. It lets an administrator describe departments, positions, responsibilities, capabilities, reporting lines, and who is appointed to a position. It is the structural layer for later cross-role workflows.

This phase keeps Octop's existing Agent execution, Team, permission, and tool-grant models intact. A position's duties and capability labels are descriptive metadata. Assigning an Agent to a position does not start it, grant it credentials, or grant additional tools.

## Data model

| Entity | Responsibility |
| --- | --- |
| Organization | Tenant-like boundary, owner, name, and description |
| Organization member | User's membership and organization role |
| Department | Nested departments inside an organization |
| Position | Duties, capability labels, department, and reporting position |
| Appointment | Links a position to a user or Agent |

All external identifiers are ULIDs; numeric IDs remain internal database keys. The v21 migration includes SQLite and PostgreSQL forms. Organization deletion cascades its structure and memberships. Department and position deletion clears references to the deleted node. Application validation prevents cross-organization parent, department, and reporting links and rejects cycles.

## Access rules

- An organization owner can edit organization details and manage members.
- An owner or manager can edit departments, positions, and appointments.
- Any member can read the organization chart.
- A user appointment requires active organization membership.
- An Agent appointment requires the actor to be an administrator, the Agent owner, or have access to a shared Agent.
- Organization membership and appointments do not change existing Agent access, RBAC, credentials, or tool grants.
- System administrators can administer charts through the existing administrator identity.

## Current product surface

The dashboard exposes organization selection and creation, a chart of departments and positions, membership management, responsibility/capability editing, and user/Agent appointment editing. User and Agent references are currently entered by ID; directory-backed selectors are a useful follow-up.

The HTTP API is under `/api/organizations`. It returns 403 for organization access violations, 404 for unavailable or cross-organization references, 409 for hierarchy cycles and conflicting assignments, and 422 for malformed or null values.

## Scenario execution increment

Enterprise scenes are now stored against one organization position and one Agent already appointed to that position. A scene contains a name, task prompt, and explicitly selected MCP connectors. Only an owner or manager can create or edit it; the creator must also own or administer the chosen Agent and must have access to the selected connectors.

Organization members who already have access to the appointed Agent can request a one-shot run; an organization appointment alone does not grant Agent access. Each request stores an immutable snapshot of the prompt, connector names, Agent, position, and scene name. An organization owner or manager other than the requester can reject it or approve it. Before queueing, Octop checks that the Agent is still enabled and appointed, then revalidates the requested connectors against the requester's current grants. Approved runs are submitted through the existing CronManager as disabled, one-shot Cron jobs. Scene jobs use an exact MCP connector allowlist and do not inherit the Agent's default connector list; the connector grant is checked again when execution loads it.

The dashboard displays scene definitions, pending approvals, and run history. It refreshes statuses from Cron's last-run result. Scene edits create immutable numbered versions; updates include the version the editor loaded, so concurrent edits return a conflict instead of overwriting one another. Runs retain the version and prompt/connector snapshot they were requested from. Audit events record create/edit/request/approve/reject actions without writing task prompts or credentials to the audit log. The v22 SQLite and PostgreSQL migration pair adds scene version history; startup also idempotently repairs databases missing the scene tables or version columns.

This increment deliberately supports one Agent and one prompt per manually requested run. Approval covers the whole prompt and connector allowlist; it does not constrain individual tools inside a selected MCP connector to read-only operations. Configure read-only connectors and an appropriately restricted Agent for this phase. The feature does not import or execute the YAML design template, schedule recurring scenes, compose multi-step graphs, retry failed work, or offer per-step approval for high-risk writes. A queued run can be monitored in the scene history; automatic recovery of an interrupted in-flight run is not implemented.

## Follow-up product increment

1. Add reusable scenario templates and controlled recurring triggers with explicit scope, idempotency, timeout, and failure policies.
2. Compose multi-step flows with step-level execution state, handoffs, retry rules, and evidence-linked outputs.
3. Add authorized high-risk actions only with step-level approval, credential scope, and audit evidence.
4. Build a team view for progress, handoffs, blocked work, and reports.
5. Add directory-backed employee/Agent pickers and an owner-transfer lifecycle.

Scenario execution should call the existing capability and permission layers. It must not infer authorization from role duties, capability labels, or organizational reporting lines.
