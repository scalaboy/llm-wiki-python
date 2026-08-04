---
title: "多Agent协作"
type: concept
tags: [ai, agent, collaboration]
sources: [wanclaw-agent-platform]
last_updated: 2026-07-05
---

[[多Agent协作]]是指多个[[AI智能体]]协同工作，各自承担不同角色，通过消息传递、任务编排和结果聚合来完成复杂的工作流程。

[[WanClaw]]支持多Agent协作，其[[多Agent路由]]机制负责将用户请求分发给最适合的Agent，并协调Agent间的通信与数据共享。

## 关键要素

- **Agent角色定义**：不同Agent负责不同职能（如信息检索、数据分析、流程执行）
- **任务编排**：将复杂任务分解为子任务分配给不同Agent
- **会话隔离与共享**：保证各Agent独立工作同时能够访问共享上下文

## 相关概念

- [[主动智能体管理平台]]
- [[工具调用]]
- [[记忆系统]]