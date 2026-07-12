export type WorkspaceLocation =
  | { view: "chat" }
  | { view: "ingestion"; jobId?: string };

type NavigateWorkspaceOptions = {
  replace?: boolean;
};

const WORKSPACE_LOCATION_EVENT = "xhbx-rag:workspace-location-change";

export function parseWorkspaceLocation(search: string): WorkspaceLocation {
  const params = new URLSearchParams(search);
  if (params.get("view") !== "ingestion") {
    return { view: "chat" };
  }

  const jobId = params.get("job");
  return jobId ? { view: "ingestion", jobId } : { view: "ingestion" };
}

export function workspaceSearch(location: WorkspaceLocation): string {
  if (location.view === "chat") {
    return "";
  }

  const params = new URLSearchParams({ view: "ingestion" });
  if (location.jobId) {
    params.set("job", location.jobId);
  }
  return `?${params.toString()}`;
}

export function navigateWorkspaceLocation(
  location: WorkspaceLocation,
  options: NavigateWorkspaceOptions = {}
): void {
  const nextUrl = `${window.location.pathname}${workspaceSearch(location)}${window.location.hash}`;
  const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (nextUrl === currentUrl) {
    return;
  }

  const method = options.replace ? "replaceState" : "pushState";
  window.history[method](null, "", nextUrl);
  window.dispatchEvent(new Event(WORKSPACE_LOCATION_EVENT));
}

export function subscribeWorkspaceLocation(
  listener: (location: WorkspaceLocation) => void
): () => void {
  const handlePopState = () => {
    listener(parseWorkspaceLocation(window.location.search));
  };
  window.addEventListener("popstate", handlePopState);
  window.addEventListener(WORKSPACE_LOCATION_EVENT, handlePopState);
  return () => {
    window.removeEventListener("popstate", handlePopState);
    window.removeEventListener(WORKSPACE_LOCATION_EVENT, handlePopState);
  };
}
