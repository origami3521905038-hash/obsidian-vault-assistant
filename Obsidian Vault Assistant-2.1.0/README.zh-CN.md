# Obsidian Vault Assistant

Obsidian Vault Assistant 是一个本地优先的 Codex skill 和 MCP 服务，帮助用户把 Obsidian Markdown 库变成可检索、可维护的知识系统。它会发现配置范围内的多个 vault，按 Raw、证据/中间层、Wiki 三层检索，只读取相关章节，并把每次写入先变成可审阅计划。

## 能力

- 检查操作系统、CPU 架构、Python、Obsidian 安装状态和候选 vault 根目录。
- 在 macOS、Windows x86_64 和 Linux 上发现可用包管理器时生成安装计划；没有明确确认不会执行安装器。
- 支持 iCloud Drive、本地目录，以及 `OBSIDIAN_VAULT_ROOT`/`OBSIDIAN_VAULT_PATH` 配置的多个 vault。
- 速度与准确度平衡：优先查中间层和 Wiki，必要时或结构化层没有命中时再回 Raw。
- 归档上传文件，并拆成原始附件、Raw 笔记和 evidence 卡片；不支持的二进制文件只归档，不猜测内容。
- 使用 SHA-256 指纹、排他创建、路径检查和 plan + confirm 双阶段写入。

云同步不会由本 skill 自动配置。iCloud Drive、OneDrive、Syncthing 或其他同步服务请由用户自行设置。

## 在 Codex 中安装

1. 下载或克隆本仓库到 Codex 支持的本地插件目录。
2. 保持仓库根目录结构不变，使 `.codex-plugin/plugin.json`、`.mcp.json`、`skills/` 和 `scripts/` 位于同一层。
3. 重新加载 Codex，调用 **Obsidian Vault Assistant**。
4. 第一次使用时先执行环境检查，再按返回的 vault 选择和结构初始化计划操作。

MCP 配置使用 `python3`、相对脚本路径和 `cwd: "."`，不含机器专属绝对路径。Windows 请确保 Python 的 `python3` 命令可用；如果本机只有 `python`，在本地安装副本中把命令改为 `python`。

## 查询示例

```text
check_environment()
list_vaults()
get_vault_profile(vault_path=...)
search_tiered(query="...", scope="auto", verify_with_raw=false)
read_note_section(vault_path=..., file_path=..., heading=...)
```

基于知识库的回答必须包含：调查结论、Raw 溯源、可信度分析。找不到相关笔记时要明确说明，不能把没有证据的常识写成库内事实。

## 上传示例

```text
inspect_uploaded_file(upload_path="...", vault_path=...)
plan_file_ingest(upload_path="...", vault_path=..., title="...", claim="...")
apply_vault_plan(plan_id="...", confirm=true)  # 仅在用户确认准确计划后调用
```

完整 SOP 见 [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) 和 [docs/TUTORIAL.md](docs/TUTORIAL.md)。本地隐私边界见 [SECURITY.md](SECURITY.md)。

## 开发检查

```bash
python3 -m py_compile scripts/environment.py scripts/vault_server.py
python3 -m unittest -v tests/test_dialogues.py
```

测试只在临时 vault 中写入，不会修改用户真实 Obsidian 笔记。

## 许可证

MIT，见 [LICENSE](LICENSE)。
