"""调用 DSH headless agent 完成一轮问答。

底层命令（config.yaml 的 dsh.cmd 可覆盖）：
  npx -y @deepseek-ai/dsh --profile headless "<job>"

默认会自动查找 npx 缓存里已安装的 dsh 入口，直接用 node 调用
（快、不查 registry）；找不到再回退 npx。

headless profile 首次运行会自动初始化；LLM 凭据与 Web GUI 共用
（~/.dsh/.credentials.yaml），跑过 `dsh web` 的机器可直接用。
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
from pathlib import Path

LOG = logging.getLogger(__name__)

_DEFAULT_CMD = ["npx", "-y", "@deepseek-ai/dsh", "--profile", "headless"]
_WIN_BARE_NAMES = {"npx", "npm"}


def _find_installed_dsh_bin() -> str | None:
    """在 npx 缓存里找已安装的 dsh 入口（无需网络）。"""
    if os.name != "nt":
        return None
    cache_root = Path(os.environ.get("LOCALAPPDATA", "")) / "npm-cache" / "_npx"
    if not cache_root.is_dir():
        return None
    candidates = sorted(
        glob.glob(str(cache_root / "*" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"))
    )
    return candidates[-1] if candidates else None


def _resolve_base_cmd(cfg_cmd) -> list[str]:
    """确定 dsh 调用前缀：配置 > npx 缓存里的 dsh 入口 > npx。"""
    if cfg_cmd:
        return list(cfg_cmd)
    installed = _find_installed_dsh_bin()
    if installed:
        LOG.info("使用已安装的 dsh 入口: %s", installed)
        return ["node", installed, "--profile", "headless"]
    return list(_DEFAULT_CMD)


def _resolve_win_cmd(cmd: list[str]) -> list[str]:
    """Windows 下 npx/npm 是 .cmd 包装，CreateProcess 不识别裸名，需换成 .cmd 全路径。"""
    if os.name != "nt" or not cmd:
        return cmd
    name = os.path.basename(cmd[0])
    if name in _WIN_BARE_NAMES:
        resolved = shutil.which(name + ".cmd")
        if resolved:
            return [resolved, *cmd[1:]]
    return cmd


class DshAgent:
    def __init__(self, cfg: dict):
        self.cmd = _resolve_win_cmd(_resolve_base_cmd(cfg.get("cmd")))
        self.workdir = cfg.get("workdir")
        self.timeout = int(cfg.get("timeout_seconds", 300))
        self.system_prompt = cfg.get("system_prompt", "")

    def ask(self, user_id: str, prompt: str, timeout: int | None = None) -> str:
        job = f"{self.system_prompt}\n\n{prompt}" if self.system_prompt else prompt
        cmd = [*self.cmd, job]
        LOG.info("调用 dsh（job 约 %d 字符）", len(job))
        proc = subprocess.run(
            cmd,
            cwd=self.workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.timeout,
        )
        out = (proc.stdout or "").strip()
        if proc.returncode != 0:
            tail = (proc.stderr or "")[-2000:]
            raise RuntimeError(f"dsh 退出码 {proc.returncode}: {tail}")
        return self._extract_answer(out)

    @staticmethod
    def _extract_answer(stdout: str) -> str:
        """headless 打印最终答案。若真实输出带外壳（如 <final>…</final>），在此剥离。"""
        # TODO: 冒烟测试后按真实输出格式收紧
        return stdout
