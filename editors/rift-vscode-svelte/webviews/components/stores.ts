import { readable, writable } from "svelte/store";
import { DEFAULT_STATE } from "../../src/types";
import type { WebviewState } from "../../src/types";

export const state = readable<WebviewState>(DEFAULT_STATE, (set) => {
  const handler = (event: any) => {
    if (event.data.type != "stateUpdate")
      throw new Error(
        `Message passed to webview that is not stateUpdate: ${event.data.type}`,
      );
    const newState = event.data.data as WebviewState;
    set(newState);
  };

  window.addEventListener("message", handler);
  vscode.postMessage({ type: "refreshState" });

  return () => window.removeEventListener("message", handler);
});

export const dropdownOpen = writable(false);
// export const state = writable<WebviewState>(DEFAULT_STATE)
// export const progressResponse = writable('')
