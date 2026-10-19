# Product-neutral knowledge source integration

Virtual workers can bind knowledge bases from multiple products through the same governed MCP contract. Octop stores a provider identifier and a product's MCP server/tool mapping; product credentials stay in the existing encrypted connector store.

## Bind a product knowledge base

The connector must already be present in an enabled enterprise scene for the worker's Agent and position. A manager then calls:

```http
PUT /api/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-sources
```

Example for the built-in RAGFlow adapter:

```json
{
  "expected_capability_version": 4,
  "sources": [
    {
      "provider": "ragflow",
      "mcp_server": "ragflow__1",
      "knowledge_base_ref": "service-dataset-8",
      "selector_argument": "dataset_id",
      "read_tools": ["retrieve"],
      "forbidden_arguments": []
    }
  ]
}
```

Octop's RAGFlow connector adapts the RAGFlow REST retrieval API to the common MCP contract. It sends the bound `knowledge_base_ref` as the only `dataset_ids` entry; the Agent cannot broaden the search to another dataset. Retrieval is read-only and the API key stays encrypted with the connector.

`provider` is a stable product key chosen by the connector owner. `knowledge_base_ref` is the product's stable dataset, space, or base identifier. `selector_argument` names the top-level MCP argument that selects that base; omit it when the connector credential is already scoped to one base. `read_tools` lists the MCP tool names as shown by Octop, without the server prefix. `forbidden_arguments` can prohibit alternate selector fields exposed by a product's tool schema.

## Runtime enforcement

When a scene run is requested, Octop snapshots the source bindings with the plan. Approval and execution both compare that snapshot with the worker's current bindings and the scene's MCP whitelist. During the approved run, middleware blocks calls to a bound server unless the tool is in its `read_tools` list. If a selector is configured, Octop injects the approved reference when unambiguous and rejects a different reference. Multiple bases on one server require an explicit approved selector.

Use product credentials that have read-only permission as well. The tool allowlist controls what the Octop Agent may call; the knowledge product remains responsible for enforcing its own authorization and tenant boundaries.

## Operational diagnostics

An organization manager can run an on-demand check for every source bound to a worker:

```http
POST /api/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-sources/diagnose
```

The response reports connector visibility/status, live upstream probe result, credential expiry, and a 90-day key-rotation reminder. It never returns credentials or raw upstream error text. Expired credentials are flagged without making an upstream request.

## Product compatibility

This contract supports products that can be reached through Octop's MCP connector flow. The tool names and selector argument are configured per source, so the runtime does not depend on AKB-specific names. A product without an MCP server needs a dedicated Octop adapter that maps its API to the same read-tool and selector contract. AKB's existing `/akb-knowledge-sources` route remains available and is translated to the generic contract internally.
