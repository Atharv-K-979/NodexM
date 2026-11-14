import { useEffect, useRef, useState } from "react";

/* Minimal types for Google Identity Services (https://accounts.google.com/gsi/client). */
interface GsiCredentialResponse {
  credential: string;
}
interface GsiButtonOptions {
  type?: "standard" | "icon";
  theme?: "outline" | "filled_blue" | "filled_black";
  size?: "large" | "medium" | "small";
  text?: "signin_with" | "signup_with" | "continue_with" | "signin";
  shape?: "rectangular" | "pill" | "circle" | "square";
  logo_alignment?: "left" | "center";
  width?: number;
}
interface GoogleIdentity {
  accounts: {
    id: {
      initialize: (o: { client_id: string; callback: (r: GsiCredentialResponse) => void; ux_mode?: "popup"; cancel_on_tap_outside?: boolean }) => void;
      renderButton: (el: HTMLElement, o: GsiButtonOptions) => void;
      disableAutoSelect: () => void;
    };
  };
}
declare global {
  interface Window {
    google?: GoogleIdentity;
  }
}

const SCRIPT_SRC = "https://accounts.google.com/gsi/client";
let scriptPromise: Promise<GoogleIdentity> | null = null;

function loadGoogle(): Promise<GoogleIdentity> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google);
  if (!scriptPromise) {
    scriptPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = SCRIPT_SRC;
      s.async = true;
      s.defer = true;
      s.onload = () => (window.google ? resolve(window.google) : reject(new Error("Google sign-in did not load")));
      s.onerror = () => {
        scriptPromise = null;
        reject(new Error("Could not load Google sign-in. Check your connection or ad blocker."));
      };
      document.head.appendChild(s);
    });
  }
  return scriptPromise;
}

export function googleSignOut() {
  window.google?.accounts.id.disableAutoSelect();
}

interface Props {
  clientId: string;
  onCredential: (credential: string) => Promise<void>;
  onError: (message: string) => void;
}

/** Google's own "Continue with Google" button (Google requires its official button). */
export default function GoogleButton({ clientId, onCredential, onError }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const handlers = useRef({ onCredential, onError });
  handlers.current = { onCredential, onError };

  useEffect(() => {
    let cancelled = false;
    loadGoogle()
      .then((google) => {
        if (cancelled || !ref.current) return;
        google.accounts.id.initialize({
          client_id: clientId,
          ux_mode: "popup",
          cancel_on_tap_outside: true,
          callback: (r) => {
            handlers.current.onCredential(r.credential).catch((e: Error) => handlers.current.onError(e.message));
          },
        });
        const width = Math.min(400, Math.max(200, Math.round(ref.current.getBoundingClientRect().width)));
        google.accounts.id.renderButton(ref.current, {
          type: "standard",
          theme: "filled_black",
          size: "large",
          text: "continue_with",
          shape: "rectangular",
          logo_alignment: "left",
          width,
        });
        setState("ready");
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setState("failed");
          handlers.current.onError(e.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  return (
    <div className="relative min-h-[44px] w-full">
      {state === "loading" && <div className="skeleton absolute inset-0 h-11 rounded-lg" aria-hidden />}
      <div ref={ref} className={`flex w-full justify-center ${state === "ready" ? "" : "invisible"}`} />
    </div>
  );
}
