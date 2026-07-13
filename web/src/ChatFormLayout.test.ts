// @ts-expect-error Vitest runs in Node, while the app tsconfig intentionally omits Node types.
const nodeFs = await import("node:fs");
const styles = (
  nodeFs as { readFileSync: (path: string, encoding: "utf8") => string }
).readFileSync("src/styles.css", "utf8");

test("桌面端发送按钮靠最右显示", () => {
  expect(styleBlock(".form-actions")).toContain("justify-content: flex-end;");
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
