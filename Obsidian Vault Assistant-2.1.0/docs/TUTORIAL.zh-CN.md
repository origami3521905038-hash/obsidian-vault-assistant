# 实用教程

## 1. 第一次使用

先调用 `check_environment`，它只读。如果没有安装 Obsidian，再调用 `plan_environment_setup`，向用户展示 `plan_id` 和完整的包管理器命令并请求确认。只有在用户确认后，才调用 `apply_environment_setup(plan_id, confirm=true)`。如果返回 `manual_required`，请用户自行从 <https://obsidian.md/download> 安装，再次执行环境检查。

随后调用 `list_vaults`。使用返回的完整 `vault_path` 选择目标；当 vault 重名时不能只传名称。再调用 `get_vault_profile` 和 `audit_vault_structure` 了解现有结构。完全没有 vault 时，调用 `plan_new_vault` 指定本地根目录，审阅目录和模板后再确认应用。

## 2. 查询流程

普通问题使用 `search_tiered(scope="auto")`。服务先查 evidence/中间层和 Wiki，没有结构化命中时才查 Raw。用户要求原文、日期、访谈或冲突核对时设置 `verify_with_raw=true`。从结果中取出标题，用 `read_note_section` 读取对应章节，避免把整篇长文塞进上下文。

回答固定包含：

1. 调查结论，并标注是库内事实还是综合推断。
2. Raw 溯源，写明 vault 名称、相对路径和章节；没有 Raw 时明确说没有找到。
3. 可信度分析，说明证据等级、冲突、日期、来源限制和待核验事项。

结果为空时，要说明知识库没有直接证据，并列出搜索过的 vault 和层级。

## 3. 上传流程

先调用 `inspect_uploaded_file`，再决定目标 vault。它在本地读取、计算 SHA-256、判断文件类型并给出 vault 建议。建议不明确时不要自动选择。选定 vault 后调用 `plan_file_ingest`，计划会包含：

- `raw/YYYY-MM/attachments/` 下的原始附件；
- 保存提取内容和来源信息的 Raw Markdown 笔记；
- 包含 claim、实体、主题、证据等级、Raw 链接和下一步的 evidence 卡片；
- 可选的 Wiki 候选（只是建议，不是修改）。

Markdown/文本、CSV、JSON、YAML、HTML/XML 和 DOCX 使用本地标准库提取。PDF、图片、压缩包和未知二进制文件标记为 `archive_only`，evidence 卡片保持“待验证”，不会猜测内容。

向用户展示全部目标路径、预览、源文件哈希和证据等级。只有用户明确同意准确计划后，才能调用 `apply_vault_plan`。服务会拒绝过期计划、已改变的上传文件、路径穿越、隐藏目录、已存在目标以及指纹变化的 Wiki 章节。

## 4. 云同步与隐私

服务不会启用云同步，也不会把笔记或上传内容发送到远程接口。云同步请在 Obsidian 或操作系统中自行配置。构建和测试期间使用临时 vault，并在最终验证前后比较真实 Markdown 文件的哈希清单。
