"""
The agent as a service (domain-free; the pack comes from config/project.toml or --pack).

- core.py: AgentService -- one question in, one audited answer out (routed plan, dry run, its own run
  folder), with back-pressure and progress events.
- http.py: the HTTP API (JSON and Server-Sent Events), standard library only.
- mcp.py: the MCP server (one tool per live playbook, plus "ask"), on stdio or on HTTP POST /mcp.

    python -m agentkit.service --http 127.0.0.1:8780 [--sim DIR] [--skip-llm]
    python -m agentkit.service --mcp-stdio [--sim DIR] [--skip-llm]
"""

from agentkit.service.core import AgentService, BadRequest, Busy

__all__ = ["AgentService", "BadRequest", "Busy"]
