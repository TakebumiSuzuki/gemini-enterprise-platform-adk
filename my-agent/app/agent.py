# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import pathlib

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.environment import LocalEnvironment
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.environment import EnvironmentToolset
from google.adk.tools.load_web_page import load_web_page
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types

MODEL = "gemini-3.5-flash"
_RETRY = types.HttpRetryOptions(attempts=3)

_APP_DIR = pathlib.Path(__file__).parent

# システムプロンプトは外部ファイルから読み込む（編集は app/instruction.md で行う）。
INSTRUCTION = (_APP_DIR / "instruction.md").read_text(encoding="utf-8").strip()

# (A) 作業フォルダ。エージェントはここでファイルを読み書き・編集し、コマンドを実行する。
# スキルが前提とする「入力を読む→中間ファイルを書く→scripts/ を回す→成果物を出力」という
# ファイル中心のワークフローの土台になる。
_WORKSPACE = pathlib.Path("./workspace")


def _make_model() -> Gemini:
    return Gemini(model=MODEL, retry_options=_RETRY)


def _workspace_toolset() -> EnvironmentToolset:
    """./workspace で Execute / ReadFile / EditFile / WriteFile を提供する toolset。

    注: EnvironmentToolset / LocalEnvironment は ADK 上 EXPERIMENTAL 扱い。
    """
    return EnvironmentToolset(environment=LocalEnvironment(working_dir=_WORKSPACE))


def _load_skills() -> list:
    """app/skills/ 配下の SKILL.md を持つ各フォルダをスキルとして読み込む。"""
    skills_dir = _APP_DIR / "skills"
    if not skills_dir.is_dir():
        return []
    return [
        load_skill_from_dir(d)
        for d in sorted(skills_dir.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]


# (B) SkillToolset は list_skills / load_skill / load_skill_resource / run_skill_script を
# 提供する。スクリプトの実行や成果物の入出力は上の EnvironmentToolset（Execute / ファイル
# 操作）で行うため、ここでは code_executor を渡さない。
skill_toolset = SkillToolset(skills=_load_skills())

# Web 検索は他ツールと同居できない制約があるため、専用サブエージェントに分離して
# AgentTool でラップし、root から呼び出す。
search_agent = Agent(
    name="search_agent",
    model=_make_model(),
    description="Google 検索で最新情報を調べ、出典付きで簡潔に回答する。",
    instruction=(
        "You are a web search specialist. Given a query, use google_search to "
        "find relevant information and return a concise, sourced summary."
    ),
    tools=[google_search],
)

# (C) 汎用サブエージェント: スキルが「subagent / Agent tool で検証させろ」と指示する工程
# （narrative-to-slide-outline の QA、compose-slide-narrative の出典検証など）の委譲先。
# 自己完結したタスクプロンプトを受け取り、./workspace のファイルを読んで検証し報告する。
general_agent = Agent(
    name="general_agent",
    model=_make_model(),
    instruction=(
        "You are a general-purpose worker agent used for delegated subtasks "
        "such as QA and source verification. You receive a self-contained task "
        "prompt that includes everything you need (paths, rules, what to check). "
        "Complete the task by reading the relevant files in ./workspace and "
        "running the requested checks, then report your findings concisely and "
        "factually. Do not invent data; if a file is missing or unreadable, say "
        "so explicitly."
    ),
    tools=[_workspace_toolset()],
)

root_agent = Agent(
    name="root_agent",
    model=_make_model(),
    instruction=INSTRUCTION,
    tools=[
        _workspace_toolset(),            # (A) ファイル操作＋コマンド実行
        load_web_page,                   # (D) 特定 URL の本文取得
        AgentTool(agent=search_agent),   # Web 検索（分離済み）
        AgentTool(agent=general_agent),  # (C) QA / 検証の委譲先
        skill_toolset,                   # pptx / narrative 等のスキル
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
