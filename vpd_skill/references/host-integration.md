# 宿主 Agent 最小接入契约

核心逻辑全部在 `vpdtest`（Python 包 + CLI），与宿主无关。宿主只需要：

1. **运行命令**：能在 `vpd_skill` 目录执行 `.\vpdtest.ps1 ...`（或 `.venv\Scripts\python.exe -m vpdtest ...`）并读取 stdout。
2. **读取 JSON**：stdout 是单个 JSON 对象；退出码 0 = 命令完成（不代表用例通过），1 = `--fail-on` 命中，3 = 参数或工具错误（`{"error", "message"}`）。
3. **读文件**：能打开返回的 `report.md`、`obs/<id>/observation.json`，可选能查看 `screen.png`（用于未知页面理解与 UI/UX 建议）。
4. **加载指令**：把 `SKILL.md` 作为技能说明加载；`references/` 按需读取。

不需要：常驻服务、数据库、网络（除 hdc 连接设备外）、多 Agent 编排。状态都在 `data/` 与 `runs/` 文件中，
任一宿主会话中断后可以从文件继续。

## 已知宿主的挂载方式

- **Claude Code**：`install.ps1 -LinkSkill` 在 `%USERPROFILE%\.claude\skills\vpd-app-test` 建目录联接指向本目录。
- **其他支持 SKILL.md 的宿主**：`install.ps1 -LinkSkill -SkillDir <宿主技能目录>\vpd-app-test`（路径以宿主文档为准）。
- **只支持 system prompt / 工具调用的宿主**：把 SKILL.md 正文放进指令，把 `vpdtest` 注册为一个 shell 工具即可。

CI 或无人值守执行时不需要 Agent：`.\vpdtest.ps1 suite motor_hm_smoke --device hdc:<serial> --fail-on failed blocked`。
