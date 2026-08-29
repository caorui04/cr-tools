# 学习路径（docs/onboarding/）

> 面向使用者（人文社科大学生，非程序员）的文档入口。本目录是 M4 交付物之一。
> 状态：**骨架已建，正文按用户决策押后到验收阶段编写**（引用操作流程手册为核心章节，工作纪要见 `../experience/010-manual-writing-brief.md`）。

## 目录规划

| 文件 | 内容 | 状态 |
|---|---|---|
| `上手指南.md` | 30 秒认识论文工作台（界面布局/四大入口/常用操作） | 🔵 押后（验收阶段） |
| `引用操作指南.md` | **核心章节**：引用操作全流程（写论文引用 / 与 AI 讨论引用 / 检索引用 / 引用链面板），围绕"降低引用操作工作量" | 🔵 押后（验收阶段） |
| `常见问题.md` | 引用不命中/超窗、判重、入库监控、Key 配置等 FAQ | 🔵 押后（验收阶段） |

## 编写依据（正文编写时对照）

- 产品主线（用户定案）：**引用是核心功能，主要目的 = 降低正常引用的操作工作量，功能设计围绕此展开**。
- 流程事实：`cr-plugin/lib/client.js`（U2/U3/T14/T10 登记链路）+ `docs/plans/m2-citation-chain.md` §2.1 + `tickets/T16-m3-e-paste-cite.md` §2。
- 工作纪要：`docs/experience/010-manual-writing-brief.md`（结构初拟/写作依据/纪律）。
- 快速上手入口另见仓库根 `README.md`。

## 学习路径主线（供未来维护者）

1. 读 `README.md`（产品定位）→ `PROJECT_KICKOFF.md`（架构与里程碑）→ `docs/adr/INDEX.md`（决策契约）。
2. 功能使用走 `docs/onboarding/`（本目录）。
3. 参与维护：`AGENTS.md`（Agent 协作规则）→ `docs/experience/`（经验库）。
