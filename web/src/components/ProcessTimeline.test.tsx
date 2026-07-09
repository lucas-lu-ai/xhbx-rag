import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProcessTimeline } from "./ProcessTimeline";
import type { AnswerProcessStep } from "../types";

const steps: AnswerProcessStep[] = [
  { step: "query_understanding", message: "已完成问题理解" },
  { step: "rerank", message: "已完成证据重排" }
];

test("运行中默认展开并显示步骤", () => {
  render(<ProcessTimeline active steps={steps} />);

  expect(screen.getByRole("button", { name: /处理过程/ })).toHaveAttribute(
    "aria-expanded",
    "true"
  );
  expect(screen.getByText("已完成问题理解")).toBeInTheDocument();
  expect(screen.getByText("运行中")).toBeInTheDocument();
});

test("运行结束后自动折叠，点击可重新展开、再点击折叠", async () => {
  const user = userEvent.setup();
  const { rerender } = render(<ProcessTimeline active steps={steps} />);

  rerender(<ProcessTimeline active={false} steps={steps} />);

  const toggle = screen.getByRole("button", { name: /处理过程/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("已完成证据重排")).not.toBeInTheDocument();

  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("已完成证据重排")).toBeInTheDocument();

  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("已完成证据重排")).not.toBeInTheDocument();
});

test("历史回答的处理过程默认折叠", () => {
  render(<ProcessTimeline active={false} steps={steps} />);

  expect(screen.getByRole("button", { name: /处理过程/ })).toHaveAttribute(
    "aria-expanded",
    "false"
  );
  expect(screen.queryByText("已完成问题理解")).not.toBeInTheDocument();
});

test("没有步骤且非运行中时不渲染", () => {
  const { container } = render(<ProcessTimeline active={false} steps={[]} />);

  expect(container).toBeEmptyDOMElement();
});

test("运行中但还没有步骤时展开显示连接提示", () => {
  render(<ProcessTimeline active steps={[]} />);

  expect(screen.getByText("正在连接问答服务...")).toBeInTheDocument();
});
