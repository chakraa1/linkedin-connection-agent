"""
Boolean Search Tool — generates LinkedIn Boolean search strings for a given ICP.
Wraps Claude as a CrewAI-compatible tool.
"""
import json
from pathlib import Path

import yaml
from anthropic import Anthropic
from crewai.tools import BaseTool

ICP_CONFIG_PATH = Path("config/icp_config.yaml")


class BooleanSearchTool(BaseTool):
    name: str = "Boolean Search Generator"
    description: str = (
        "Generates optimized LinkedIn Boolean search strings for a given ICP key. "
        "Input: ICP key string (e.g. 'icp1'). "
        "Output: JSON array of {query, rationale} objects."
    )

    def _run(self, icp_key: str = "icp1") -> str:
        with open(ICP_CONFIG_PATH, encoding="utf-8") as f:
            icp_config = yaml.safe_load(f)

        icp = icp_config.get(icp_key) or icp_config.get("icp1")
        if not icp:
            return f"ICP key '{icp_key}' not found in icp_config.yaml"

        client = Anthropic()
        prompt = f"""Generate 6-8 LinkedIn Boolean search strings for this ICP.

ICP: {icp['name']}
Target roles: {json.dumps(icp['target_roles'])}
Industries: {json.dumps(icp['industries'])}
Locations: {json.dumps(icp['locations'])}
Keywords: {json.dumps(icp['keywords'])}

Rules:
- Use AND, OR, NOT (uppercase)
- Use quotes for exact phrases
- Each string must be 200 characters or fewer
- Vary approaches: title-focused, skill-focused, industry-focused

Return ONLY a valid JSON array:
[{{"query": "...", "rationale": "one-line rationale"}}, ...]"""

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
