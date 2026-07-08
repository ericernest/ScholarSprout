"""定义 NoviceSynapse 的轻量 agent。"""

from __future__ import annotations

from dataclasses import dataclass

from models.client import OpenAIClient

from .profiles import Profiles


# 描述 agent 预设的运行时访问对象。
@dataclass(slots=True)
class AgentProfile:
    name: str
    type: str
    role: str
    system_prompt: str
    tools: list[str]


# 根据 agent type 和 llm 生成运行时 agent。
class Agent:
    # 从预设中读取 agent 配置并绑定 llm。
    def __init__(self, agent_type: str, llm: OpenAIClient, profiles: Profiles | None = None):
        profile = (profiles or Profiles()).get(agent_type)
        self.profile = AgentProfile(
            name=str(profile["name"]),
            type=str(profile["type"]),
            role=str(profile["role"]),
            system_prompt=str(profile["system_prompt"]),
            tools=list(profile["tools"]),
        )

        self.agent_type = agent_type
        self.llm = llm
        self.system_prompt = self.profile.system_prompt
        self.role = self.profile.role
        self.tools = self.profile.tools


# 根据 llm 和 agent type 获取可用 agent。
def create_agent(llm: OpenAIClient, agent_type: str) -> Agent:
    return Agent(agent_type=agent_type, llm=llm)
