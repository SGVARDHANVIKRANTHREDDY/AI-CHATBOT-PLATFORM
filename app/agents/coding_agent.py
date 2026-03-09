from __future__ import annotations
from typing import Any
from app.agents.agent_state import AgentState
from app.agents.task_graph import TaskNode
from app.llm.base import LLMProvider
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

class CodingAgent:
    PROMPT_KEY = "coding_agent"
    
    DEFAULT_PROMPT = """You are a Coding Agent. Your task is to write/execute code for: {task}
Context: {context}
If outputting code, prefix with ```python.
If you need to execute code, output <tool_call: code_execution(code="...")>."""

    def __init__(self, llm: LLMProvider, tool_runner: Any, prompt_manager: Any = None):
        self.llm = llm
        self.tool_runner = tool_runner
        self.prompt_manager = prompt_manager
        if self.prompt_manager:
            self.prompt_manager.initialize_prompt(self.PROMPT_KEY, self.DEFAULT_PROMPT)

    async def execute(self, node: TaskNode, state: AgentState) -> tuple[str, str]:
        _LOG.info(f"CodingAgent executing task: {node.task}")
        context = state.get_context_for_agent()
        
        version_id = "static"
        if self.prompt_manager:
            template, version_id = self.prompt_manager.get_prompt_with_id(self.PROMPT_KEY)
            full_prompt = template.format(task=node.task, context=context)
        else:
            full_prompt = self.DEFAULT_PROMPT.format(task=node.task, context=context)
        
        system_msg = "You are a senior software engineer. If you use a tool, wait for the result before continuing."
        
        # Internal Tool Loop
        max_turns = 3
        current_turn = 0
        current_prompt = full_prompt
        
        while current_turn < max_turns:
            result = await self.llm.ask(current_prompt, system_prompt=system_msg)
            
            # Use pattern from tool_runner to detect calls
            from app.orchestrator.tool_runner import StreamingToolRunner
            match = StreamingToolRunner.TOOL_PATTERN.search(result)
            
            if match:
                name = match.group(1)
                args = match.group(2)
                
                _LOG.info(f"CodingAgent calling tool: {name}")
                state.increment_tool_count()
                tool_result = await self.tool_runner.run_tool(name, args)
                
                # Feed result back to LLM
                current_prompt += f"\nObservation from {name}: {tool_result}"
                current_turn += 1
                continue
            else:
                return result, version_id
                
        return "Error: Maximum internal coding turns reached without final answer.", version_id
