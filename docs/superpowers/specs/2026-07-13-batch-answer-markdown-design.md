# 批量详情页大模型回答 Markdown 渲染设计

## 目标

批量任务详情页中的大模型回答应与聊天页保持一致，正确渲染 Markdown 加粗、列表、链接等格式，不再直接显示 Markdown 源标记。

## 当前问题与根因

聊天页使用共享的 `MarkdownMessage` 组件渲染模型回答；批量详情页的 `BatchRunView` 则把 `openQuestion.response.answer` 直接放入 `<p>`。因此浏览器只按纯文本显示 `**加粗**` 等标记，也无法生成列表结构。

## 设计

- 在 `BatchRunView` 中导入并复用现有 `MarkdownMessage`。
- 仅替换大模型回答的输出节点，批量页的加载、错误、改写问题、人工答案和证据反馈区域保持不变。
- 继续使用 `MarkdownMessage` 已有的 GFM 支持、裸 HTML 禁用策略和外链安全属性。
- 不新增 Markdown 依赖，不修改后端回答格式或接口。

## 测试

扩展批量页面测试夹具，使模型回答包含 Markdown 加粗和列表，并断言详情页生成 `<strong>` 与 `<ul>/<li>` 语义节点。先确认测试在当前纯文本实现下失败，再替换为 `MarkdownMessage` 并运行目标测试、完整前端测试及生产构建。
