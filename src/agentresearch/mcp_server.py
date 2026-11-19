"""Local stdio tools on the official MCP Python SDK."""

from typing import Annotated, Any, Literal

from .report import validate_run
from .schema import RunConfig
from .store import RunStore


def create_server(corpus, index, investigator, db_path):
    # SQLite also supports temporary databases via an empty name and memory URIs.
    # Ask the connection which file backs main before advertising resumable runs.
    with RunStore(db_path) as store:
        if not store.db.execute("PRAGMA database_list").fetchone()[2]:
            raise ValueError("serve-mcp requires a file-backed --db checkpoint")

    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from pydantic import Field

    claim_type = Annotated[str, Field(min_length=1, max_length=10_000)]
    identifier = Annotated[str, Field(min_length=1, max_length=512)]
    server = MCPServer("AgentResearch", version="0.1.0", log_level="WARNING")

    @server.tool(structured_output=True)
    def search_sources(
        claim: claim_type,
        mode: Literal["relevance", "challenge", "focus"] = "relevance",
        limit: Annotated[int, Field(ge=1, le=12)] = 12,
    ) -> dict[str, Any]:
        """Rank sources in the configured corpus for a claim."""
        return {
            "schema_version": 1,
            "corpus_digest": corpus.digest,
            "hits": index.search(claim, mode, limit),
        }

    @server.tool(structured_output=True)
    def read_source(claim: claim_type, source_id: identifier) -> dict[str, Any]:
        """Read up to two claim-relevant sentences with exact source offsets."""
        if source_id not in corpus.by_id:
            raise ToolError("unknown source ID")
        return {
            "schema_version": 1,
            "corpus_digest": corpus.digest,
            "spans": [s.to_dict() for s in index.read(claim, source_id)],
        }

    @server.tool(structured_output=True)
    def investigate(
        claim: claim_type,
        threshold: Annotated[int, Field(ge=0, le=1_000_000)],
        policy: Literal["fixed", "first_hit", "challenge", "guarded"] = "fixed",
        budget: Annotated[int, Field(ge=1, le=8)] = 4,
    ) -> dict[str, Any]:
        """Run a local neural investigation and save its trace in the configured database."""
        try:
            with investigator() as agent:
                config = RunConfig(
                    policy, budget, threshold, agent.tools.verifier.model_digest
                )
                run = agent.run(claim, config)
        except (ValueError, OSError, ImportError) as exc:
            raise ToolError(
                "investigation unavailable; check local configuration and claim"
            ) from exc
        if run["status"] == "failed":
            raise ToolError(f"investigation failed; audit saved run {run['run_id']}")
        return run

    @server.tool(structured_output=True)
    def audit_run(run_id: identifier) -> dict[str, Any]:
        """Replay a locally stored trace and return the verified assessment."""
        with RunStore(db_path) as store:
            run = store.get(run_id)
        if run is None:
            raise ToolError("unknown run ID")
        validate_run(run, corpus, index=index)
        return {
            "schema_version": 1,
            "valid": True,
            "run_id": run_id,
            "status": run["status"],
            "assessment": run["assessment"],
        }

    return server
