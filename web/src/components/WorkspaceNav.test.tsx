import { render, screen } from "@testing-library/react";

import { WorkspaceNav } from "./WorkspaceNav";

test("只显示知识问答入口并隐藏文档入库按钮", () => {
  render(<WorkspaceNav currentView="chat" onNavigate={() => {}} />);

  expect(
    screen.getByRole("button", { name: "知识问答" })
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "文档入库" })
  ).not.toBeInTheDocument();
});
