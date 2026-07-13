// @ts-expect-error Vitest runs in Node, while the app tsconfig intentionally omits Node types.
const nodeFs = await import("node:fs");
export {};

const styles = (
  nodeFs as { readFileSync: (path: string, encoding: "utf8") => string }
).readFileSync("src/styles.css", "utf8");

test("引用明细内容从顶部开始排列", () => {
  expect(styleBlock(".source-detail")).toContain("align-content: start;");
});

test("空的证据明细插槽不占据网格布局", () => {
  expect(styleBlock(".evidence-detail-slot:empty")).toContain("display: none;");
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
