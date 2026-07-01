import { render, screen } from "@testing-library/react";

import { App } from "./App";

test("renders the question workbench shell", () => {
  render(<App />);

  expect(screen.getByRole("heading", { name: "知识库问答工作台" })).toBeInTheDocument();
  expect(screen.getByLabelText("输入问题")).toBeInTheDocument();
  expect(screen.getByRole("complementary", { name: "溯源" })).toBeInTheDocument();
});
