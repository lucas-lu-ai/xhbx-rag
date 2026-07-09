import { render, screen } from "@testing-library/react";

import { MarkdownMessage } from "./MarkdownMessage";

describe("MarkdownMessage", () => {
  it("将 **加粗** 语法渲染为 strong 元素", () => {
    render(<MarkdownMessage content="面对客户 **不争辩收益高低**，顺势转换定位。" />);

    const strong = screen.getByText("不争辩收益高低");
    expect(strong.tagName).toBe("STRONG");
  });

  it("将有序列表渲染为 ol/li 结构", () => {
    const content = "建议如下：\n\n1. 转换定位\n2. 强调保全功能\n3. 客观说明限制";
    const { container } = render(<MarkdownMessage content={content} />);

    const items = container.querySelectorAll("ol > li");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("转换定位");
  });

  it("默认不渲染原始 HTML，防止 XSS", () => {
    const { container } = render(
      <MarkdownMessage content={'<img src=x onerror="alert(1)">恶意内容'} />
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("恶意内容");
  });

  it("内容为空时不渲染任何段落", () => {
    const { container } = render(<MarkdownMessage content="" />);

    expect(container.querySelector("p")).toBeNull();
  });
});
