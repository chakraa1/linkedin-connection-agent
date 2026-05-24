"""
LLM factory — reads config/llm_config.yaml and creates CrewAI LLM instances.
Supports Anthropic and OpenAI with per-agent model mapping and env-var overrides.
"""
import os
from pathlib import Path

import yaml
from crewai import LLM

CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "llm_config.yaml"


class LLMFactory:
    def __init__(self):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)
        self._cache: dict[str, LLM] = {}

    def get(self, agent_name: str) -> LLM:
        model_id = os.getenv(
            f"{agent_name.upper()}_MODEL",
            self._config["agent_llm_mapping"].get(agent_name, "anthropic/claude-sonnet-4-6"),
        )
        if model_id not in self._cache:
            self._cache[model_id] = self._create_llm(model_id)
        return self._cache[model_id]

    def _create_llm(self, model_id: str) -> LLM:
        provider, _ = model_id.split("/", 1)
        cfg = self._config["providers"].get(provider, {})
        api_key = os.getenv(cfg.get("api_key_env", ""), "")
        return LLM(model=model_id, api_key=api_key or None)
