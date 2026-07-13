import { render, screen } from "@testing-library/react";

import { WorkspaceNav } from "./WorkspaceNav";

// @ts-expect-error Vitest runs in Node, while the app tsconfig intentionally omits Node types.
const nodeFs = await import("node:fs");
const styles = (
  nodeFs as { readFileSync: (path: string, encoding: "utf8") => string }
).readFileSync("src/styles.css", "utf8");

test("只显示知识问答入口并隐藏文档入库按钮", () => {
  render(<WorkspaceNav currentView="chat" onNavigate={() => {}} />);

  expect(
    screen.getByRole("button", { name: "知识问答" })
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "文档入库" })
  ).not.toBeInTheDocument();
  expect(styleBlock(".workspace-nav")).toContain("display: flex;");
  expect(styleBlock(".workspace-nav")).toContain("justify-content: center;");
  expect(styleBlock(".workspace-nav-item")).toContain(
    "width: calc((100% - 8px) / 2);"
  );
});

function styleBlock(selector: string): string {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`, "m").exec(
    styles
  );
  if (!match) {
    throw new Error(`Missing CSS block for ${selector}`);
  }
  return match[1];
}
