import {
  navigateWorkspaceLocation,
  parseWorkspaceLocation,
  subscribeWorkspaceLocation,
  workspaceSearch
} from "./workspaceLocation";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

test("workspace location round trips ingestion job selection", () => {
  expect(parseWorkspaceLocation("?view=ingestion&job=job-1")).toEqual({
    view: "ingestion",
    jobId: "job-1"
  });
  expect(workspaceSearch({ view: "ingestion", jobId: "job-1" })).toBe(
    "?view=ingestion&job=job-1"
  );
});

test("only known views and ingestion job selection are accepted", () => {
  expect(parseWorkspaceLocation("")).toEqual({ view: "chat" });
  expect(parseWorkspaceLocation("?view=chat&job=ignored")).toEqual({
    view: "chat"
  });
  expect(parseWorkspaceLocation("?view=unknown&job=ignored")).toEqual({
    view: "chat"
  });
  expect(workspaceSearch({ view: "chat" })).toBe("");
});

test("job IDs are encoded and decoded without changing their text", () => {
  const jobId = "任务 /?#&= %";

  const search = workspaceSearch({ view: "ingestion", jobId });

  expect(search).toBe(
    "?view=ingestion&job=%E4%BB%BB%E5%8A%A1+%2F%3F%23%26%3D+%25"
  );
  expect(parseWorkspaceLocation(search)).toEqual({ view: "ingestion", jobId });
});

test("history navigation preserves pathname and hash and avoids duplicate pushes", () => {
  window.history.replaceState(null, "", "/workspace?old=1#detail");
  const pushSpy = vi.spyOn(window.history, "pushState");

  navigateWorkspaceLocation({ view: "ingestion", jobId: "job-1" });
  navigateWorkspaceLocation({ view: "ingestion", jobId: "job-1" });

  expect(window.location.pathname).toBe("/workspace");
  expect(window.location.search).toBe("?view=ingestion&job=job-1");
  expect(window.location.hash).toBe("#detail");
  expect(pushSpy).toHaveBeenCalledTimes(1);
});

test("push and replace navigation notify subscribers exactly once", () => {
  const listener = vi.fn();
  const unsubscribe = subscribeWorkspaceLocation(listener);

  navigateWorkspaceLocation({ view: "ingestion", jobId: "job-1" });
  expect(listener).toHaveBeenCalledTimes(1);
  expect(listener).toHaveBeenLastCalledWith({
    view: "ingestion",
    jobId: "job-1"
  });

  listener.mockClear();
  navigateWorkspaceLocation({ view: "ingestion", jobId: "job-1" });
  expect(listener).not.toHaveBeenCalled();

  navigateWorkspaceLocation({ view: "ingestion", jobId: "job-2" }, { replace: true });
  expect(listener).toHaveBeenCalledTimes(1);
  expect(listener).toHaveBeenLastCalledWith({
    view: "ingestion",
    jobId: "job-2"
  });
  unsubscribe();
});

test("popstate subscription exposes the parsed current location and cleans up", () => {
  const listener = vi.fn();
  const unsubscribe = subscribeWorkspaceLocation(listener);
  window.history.replaceState(null, "", "/?view=ingestion&job=job-2#timeline");

  window.dispatchEvent(new PopStateEvent("popstate"));
  unsubscribe();
  navigateWorkspaceLocation({ view: "chat" });
  window.dispatchEvent(new PopStateEvent("popstate"));

  expect(listener).toHaveBeenCalledTimes(1);
  expect(listener).toHaveBeenCalledWith({ view: "ingestion", jobId: "job-2" });
});
