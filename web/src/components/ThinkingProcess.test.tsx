import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ThinkingProcess } from "./ThinkingProcess";

test("思考进行中默认展开并显示内容", () => {
  render(<ThinkingProcess reasoning="先分析预算约束，" live />);

  expect(screen.getByRole("button", { name: /思考过程/ })).toHaveAttribute(
    "aria-expanded",
    "true"
  );
  expect(screen.getByText("先分析预算约束，")).toBeInTheDocument();
});

test("思考结束后自动折叠，点击可重新展开", async () => {
  const user = userEvent.setup();
  const { rerender } = render(
    <ThinkingProcess reasoning="先分析预算约束，" live />
  );

  rerender(
    <ThinkingProcess reasoning="先分析预算约束，再匹配可行方案。" live={false} />
  );

  const toggle = screen.getByRole("button", { name: /思考过程/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByText("先分析预算约束，再匹配可行方案。")
  ).not.toBeInTheDocument();

  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(
    screen.getByText("先分析预算约束，再匹配可行方案。")
  ).toBeInTheDocument();
});

test("历史回答的思考过程默认折叠", () => {
  render(<ThinkingProcess reasoning="历史推理内容。" live={false} />);

  expect(screen.getByRole("button", { name: /思考过程/ })).toHaveAttribute(
    "aria-expanded",
    "false"
  );
  expect(screen.queryByText("历史推理内容。")).not.toBeInTheDocument();
});

test("没有思考内容时不渲染", () => {
  const { container } = render(<ThinkingProcess reasoning="" live={false} />);

  expect(container).toBeEmptyDOMElement();
});
