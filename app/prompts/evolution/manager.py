from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from app.llm.base import LLMProvider
from app.prompts.evolution.models import PromptEvolution, PromptVersion
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class PromptEvolutionManager:
    """
    Manages the lifecycle of system prompts, enabling automated evolution
    based on performance feedback and safety guardrails.
    """

    def __init__(self, storage_path: str, llm: LLMProvider):
        self.storage_path = Path(storage_path)
        self.llm = llm
        self.evolutions: dict[str, PromptEvolution] = {}
        self._load()

    def _load(self):
        if self.storage_path.exists():
            try:
                with open(self.storage_path, encoding="utf-8") as f:
                    data = json.load(f)
                    for key, val in data.items():
                        self.evolutions[key] = PromptEvolution.model_validate(val)
            except Exception as e:
                _LOG.error(f"Failed to load prompt evolutions: {e}")

    def _save(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {k: v.model_dump() for k, v in self.evolutions.items()}
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            _LOG.error(f"Failed to save prompt evolutions: {e}")

    def get_prompt(self, prompt_key: str, use_ab_test: bool = True) -> str:
        """Retrieve the active prompt (or an A/B candidate)."""
        evo = self.evolutions.get(prompt_key)
        if not evo:
            return ""

        # For simple A/B testing, we might return a candidate version
        # But for now, returning active
        return evo.get_active().template

    async def record_feedback(self, prompt_key: str, version_id: str, score: float):
        """Record performance score for a specific version."""
        evo = self.evolutions.get(prompt_key)
        if not evo or version_id not in evo.versions:
            return

        ver = evo.versions[version_id]
        perf = ver.performance

        # Incremental average update
        new_count = perf.hit_count + 1
        perf.avg_score = ((perf.avg_score * perf.hit_count) + score) / new_count
        perf.hit_count = new_count
        perf.last_evaluated = datetime.now()

        self._save()

        # Promotion Logic: If candidate exists and has enough hits, compare with active
        if (
            hasattr(evo, "candidate_version_id") and evo.candidate_version_id == version_id and perf.hit_count >= 5
        ):  # Threshold for promotion check
            active_perf = evo.get_active().performance
            if perf.avg_score > active_perf.avg_score:
                _LOG.info(
                    f"Promoting candidate {version_id} for {prompt_key} (Score: {perf.avg_score} > {active_perf.avg_score})"
                )
                evo.active_version_id = version_id
                evo.candidate_version_id = None
            elif perf.avg_score < active_perf.avg_score * 0.9:  # Significant degradation
                _LOG.warning(
                    f"Rejecting candidate {version_id} for {prompt_key} (Degradation: {perf.avg_score} < {active_perf.avg_score})"
                )
                evo.candidate_version_id = None
            self._save()

        # Mutation Logic: If active is underperforming, generate a new candidate
        active_ver = evo.get_active()
        if (
            active_ver.performance.hit_count >= 10
            and active_ver.performance.avg_score < 0.7
            and not getattr(evo, "candidate_version_id", None)
        ):
            await self.evolve_prompt(prompt_key, feedback=f"Low performance score: {active_ver.performance.avg_score}")

    async def evolve_prompt(self, prompt_key: str, feedback: str):
        """Mutate a prompt using LLM feedback and set as candidate."""
        evo = self.evolutions.get(prompt_key)
        if not evo:
            return

        active_ver = evo.get_active()

        mutation_prompt = f"""
        You are a Prompt Optimization Expert.

        Current Prompt Template:
        {active_ver.template}

        Performance Feedback:
        {feedback}

        Your task:
        Create an improved version of this prompt that addresses the feedback while maintaining core instructions.
        Output ONLY the new template, no explanations.
        """

        new_template = await self.llm.ask(mutation_prompt, system_prompt="You are an expert prompt engineer.")
        new_template = new_template.strip()

        if not new_template or len(new_template) < 10:
            return

        new_id = str(uuid.uuid4())
        new_ver = PromptVersion(
            version_id=new_id,
            template=new_template,
            parent_version_id=active_ver.version_id,
            mutation_feedback=feedback,
        )

        evo.versions[new_id] = new_ver
        evo.candidate_version_id = new_id  # Set as candidate for A/B testing

        _LOG.info(f"Created new candidate for '{prompt_key}': {new_id}")
        self._save()

    def get_prompt_with_id(self, prompt_key: str) -> tuple[str, str]:
        """Returns (template, version_id). Handles A/B test selection."""
        evo = self.evolutions.get(prompt_key)
        if not evo:
            return "", ""

        # 20% A/B testing if candidate exists
        import random

        candidate_id = getattr(evo, "candidate_version_id", None)
        if candidate_id and random.random() < 0.2:  # noqa: S311
            return evo.versions[candidate_id].template, candidate_id

        return evo.get_active().template, evo.active_version_id

    def initialize_prompt(self, prompt_key: str, template: str):
        """Register a new system prompt if it doesn't exist."""
        if prompt_key in self.evolutions:
            return

        ver_id = str(uuid.uuid4())
        ver = PromptVersion(version_id=ver_id, template=template, is_active=True)
        evo = PromptEvolution(
            prompt_key=prompt_key, versions={ver_id: ver}, active_version_id=ver_id, baseline_version_id=ver_id
        )
        self.evolutions[prompt_key] = evo
        self._save()
