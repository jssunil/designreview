"""
Multi-provider LLM gateway -- compatibility import path.

The implementation moved to agentkit/llm/gateway.py and routing to config/routing.toml. Same
`LLMGateway().call(prompt, system_prompt=, temperature=, max_tokens=,
json_mode=, tier=) -> str` API, plus `call_detailed(...) -> LLMReply`
with real token usage and a normalised stop_reason.
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentkit.llm.gateway import (LLMGateway, LLMReply, ProviderFault, RateState, ResponseCache,  # noqa: E402
                                  ROUTING_PATH as ROUTING_CONFIG_PATH)

__all__ = ["LLMGateway", "LLMReply", "ProviderFault", "RateState", "ResponseCache", "ROUTING_CONFIG_PATH"]

if __name__ == "__main__":
    gw = LLMGateway()
    print("Testing LLM Gateway...")
    reply = gw.call_detailed("Respond with 'GATEWAY_OK' if you receive this message.", max_tokens=64)
    print(f"Gateway response ({reply.provider}/{reply.model}):", reply.text.strip())
    print("Cost ledger:", gw.ledger.summary())
