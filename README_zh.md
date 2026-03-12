# 🔮 antigravity-history

[English](README.md) | [中文](README_zh.md)

[![PyPI Version](https://img.shields.io/pypi/v/antigravity-history)](https://pypi.org/project/antigravity-history/)
[![GitHub Release](https://img.shields.io/github/v/release/neo1027144-creator/antigravity-history)](https://github.com/neo1027144-creator/antigravity-history/releases/latest)
[![Build](https://github.com/neo1027144-creator/antigravity-history/actions/workflows/publish.yml/badge.svg)](https://github.com/neo1027144-creator/antigravity-history/actions/workflows/publish.yml)
[![Python](https://img.shields.io/pypi/pyversions/antigravity-history)](https://pypi.org/project/antigravity-history/)
[![License](https://img.shields.io/github/license/neo1027144-creator/antigravity-history)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-blue)]()

> **导出和恢复你的 Antigravity AI 对话 — 完整还原每一条消息。**

完整导出 AI 思考链、代码 diff、命令输出和精确时间戳。还能恢复崩溃或更新后丢失的对话。

---

## ✨ 核心特色

| 特色 | 说明 |
|------|------|
| 🧠 **AI 思考过程** | 导出每条 AI 回复背后隐藏的推理链 |
| 📝 **代码 Diff** | 捕获每次代码编辑的完整上下文差异 |
| 💻 **命令输出** | 保存终端输出，含退出码和工作目录 |
| ⏰ **消息级时间戳** | 每条消息都有精确时间记录 |
| 🔄 **对话恢复** | 扫描磁盘 `.pb` 文件，恢复崩溃/更新后丢失的对话 |
| 📁 **自动 .pb 扫描** | 自动发现并导出未被索引的磁盘对话文件 |
| 🖥️ **零配置发现** | 自动查找所有运行中的 Antigravity 实例 |
| 🔐 **100% 本地只读** | 不联网、不追踪、不修改任何数据 |

---

## 🚀 快速开始

```bash
pip install antigravity-history==0.2.5 --index-url https://pypi.org/simple --no-cache-dir
```

```bash
aghistory export          # ← 从这里开始！
```

---

## 📋 命令参考

| 命令 | 说明 |
|------|------|
| `aghistory export` | 导出对话为 Markdown / JSON 格式 |
| `aghistory list` | 列出所有已索引的对话 |
| `aghistory recover` | 恢复崩溃/更新后丢失的对话 |
| `aghistory info` | 显示 LanguageServer 连接状态 |

### 导出详细程度（递进关系 — 高级别包含低级别全部内容）

```bash
aghistory export                     # 基础：用户消息 + AI 回复 + 工具摘要
aghistory export --thinking          # ↑ + 思维链、时间戳、退出码、工作目录
aghistory export --full              # ↑ + diff 全文、命令输出、搜索结果、模型名
```

### 导出范围（筛选条件，互相独立）

```bash
aghistory export                     # 全部对话
aghistory export --today             # 只导出今天的
aghistory export --id abc123         # 按 cascade ID 导出指定对话（可重复使用）
```

### 输出格式与路径

```bash
aghistory export                     # 默认：所有格式（md + json）
aghistory export -f md               # 仅 Markdown
aghistory export -f json             # 仅 JSON
aghistory export -o ./my_folder      # 指定输出目录
```

### 常见组合用法

```bash
# 导出今天的对话，含 AI 思维链，输出为 Markdown
aghistory export --today --thinking -f md

# 导出指定对话，包含全部细节，输出为 JSON
aghistory export --id abc123 --full -f json

# 导出全部对话到自定义目录
aghistory export -o ./my_backup
```

---

## 🔒 隐私与安全

- **100% 本地** — 所有数据留在你的电脑上，不发送任何网络请求
- **只读操作** — 不会修改、删除或写入 Antigravity 的任何数据
- **无追踪** — 无分析、无遥测、无回传
- **开源** — 你可以审计每一行代码

---

## ❓ 常见问题

<details>
<summary><b>必须开着 Antigravity 才能用吗？</b></summary>

是的。本工具通过 Antigravity 本地的 LanguageServer API 工作，只有 Antigravity 打开并加载了 workspace 时才可用。
</details>

<details>
<summary><b>会不会被封号？</b></summary>

不会。所有通信仅限于 `localhost`，没有任何请求发往外部服务器。工具只是用 Antigravity 自身的 API 读取你自己的对话数据。
</details>

<details>
<summary><b>三个导出级别是什么意思？</b></summary>

- **默认**：用户消息、AI 回复、工具调用摘要
- **`--thinking`**：+ AI 推理过程、时间戳、退出码
- **`--full`**：+ 代码 diff、命令输出、搜索摘要、模型信息
</details>

<details>
<summary><b>"恢复"是什么功能？</b></summary>

Antigravity 把对话存为磁盘上的 `.pb` 文件，但有时会在索引中丢失它们（比如崩溃或更新后）。`recover` 命令扫描这些文件并通过 API 重新加载。
</details>

---

## 📄 许可证

[Apache 2.0](LICENSE) — 自由使用，欢迎贡献。

---

<sub>如果这个工具帮到了你，欢迎给个 ⭐！</sub>
