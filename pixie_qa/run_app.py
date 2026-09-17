"""提供 Pixie 标准入口并复用仓库内 Archive Agent Runnable。"""

from evals.archive.runnable import ArchiveAgentArgs, ArchiveAgentRunnable


AppArgs = ArchiveAgentArgs
AppRunnable = ArchiveAgentRunnable


__all__ = ["AppArgs", "AppRunnable"]
