# 问答发送按钮右对齐设计

## 目标

将知识问答输入区的“发送”按钮在桌面端放置到操作栏最右侧，同时保留移动端现有的整行宽度布局。

## 实现

- 保持 `ChatView` 的 DOM 结构和发送逻辑不变。
- 在通用且目前仅由问答输入区使用的 `.form-actions` 样式中增加 `justify-content: flex-end`。
- 保留 `max-width: 920px` 媒体查询中的纵向排列和拉伸行为，使移动端按钮继续占满可用宽度。

## 方案选择

采用 Flex 容器的主轴对齐能力，不给按钮单独增加 `margin-left: auto`，也不引入只服务单按钮布局的 Grid。这样改动最小，语义直接，并与现有响应式 Flex 结构一致。

## 验证

- 增加样式回归测试，断言 `.form-actions` 包含 `justify-content: flex-end`。
- 运行相关前端测试与生产构建，确认发送行为和 TypeScript 编译不受影响。
