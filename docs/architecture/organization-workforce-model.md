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

## Next product increment

The next increment should connect the workforce structure to the scenario/template system:

1. Add scenario templates with triggers, task steps, inputs, outputs, tool requirements, and escalation policy.
2. Bind a scenario to one or more positions/Agents while keeping tool grants and credential scopes explicit.
3. Add workflow execution state, retry policy, audit events, and human approval gates for consequential actions.
4. Build a team view for progress, handoffs, blocked work, and evidence-backed reports.
5. Add directory-backed employee/Agent pickers and an owner-transfer lifecycle.

Scenario execution should call the existing capability and permission layers. It must not infer authorization from role duties, capability labels, or organizational reporting lines.
