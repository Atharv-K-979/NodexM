import { useEffect, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "thread"; id: string }
  | { name: "watches" }
  | { name: "watch"; id: number };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "t" && parts[1]) return { name: "thread", id: parts[1] };
  if (parts[0] === "watches" && parts[1] && !Number.isNaN(Number(parts[1]))) return { name: "watch", id: Number(parts[1]) };
  if (parts[0] === "watches") return { name: "watches" };
  return { name: "home" };
}

export function navigate(path: string) {
  const next = `#${path}`;
  if (window.location.hash !== next) window.location.hash = next;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
