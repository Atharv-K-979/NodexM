import { useState, type FormEvent } from "react";
import { ArrowRight, Quote, ShieldCheck, Telescope } from "lucide-react";
import { useSession } from "../session";
import GoogleButton from "./GoogleButton";
import { Logo, Spinner } from "./ui";

export default function AuthScreen() {
  const { signIn, signUp, signInWithGoogle, config } = useSession();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "signin") await signIn(email, password);
      else await signUp(email, password, name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const free = config ? `${config.free_quick_runs} Quick + ${config.free_deep_runs} Deep` : "3";

  return (
    <div className="grid min-h-full lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden overflow-hidden border-r border-line bg-side px-14 py-12 lg:flex lg:flex-col">
        <div className="flex items-center gap-2.5">
          <Logo />
          <span className="text-lg font-semibold tracking-tight">NodexM</span>
        </div>
        <div className="my-auto max-w-lg">
          <h1 className="font-serif text-[44px] font-semibold leading-[1.08] tracking-tight text-balance">
            Research answers where every claim is checked.
          </h1>
          <p className="mt-5 text-[17px] leading-relaxed text-muted">
            A team of agents searches official, academic, news and data sources, reads them in full, and tells you
            which facts the sources agree on, which they dispute, and which rest on a single source.
          </p>
          <ul className="mt-10 space-y-5 text-[15px]">
            {[
              [ShieldCheck, "Claim-level verification", "Agreed, disputed and single-source, with a confidence score."],
              [Quote, "Evidence you can click", "Every citation opens the exact sentence it came from."],
              [Telescope, "Watches that notice change", "Re-run a topic on a schedule and get told when a fact flips."],
            ].map(([Icon, title, text]) => {
              const I = Icon as typeof ShieldCheck;
              return (
                <li key={title as string} className="flex gap-3.5">
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-raised text-accent">
                    <I className="h-4 w-4" />
                  </span>
                  <span>
                    <span className="block font-medium">{title as string}</span>
                    <span className="text-muted">{text as string}</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
        <p className="text-xs text-faint">New accounts get {free} research runs free.</p>
      </section>

      <section className="flex items-center justify-center px-4 py-12">
        <form onSubmit={submit} className="w-full max-w-sm animate-rise">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <Logo />
            <span className="text-lg font-semibold">NodexM</span>
          </div>
          <h2 className="text-2xl font-semibold tracking-tight">{mode === "signin" ? "Welcome back" : "Create your account"}</h2>
          <p className="mt-1.5 text-sm text-muted">
            {mode === "signin" ? "Sign in to continue your research." : `Start with ${free} free runs. No card needed.`}
          </p>

          {config?.google_client_id && (
            <div className="mt-7">
              <GoogleButton
                clientId={config.google_client_id}
                onCredential={async (credential) => {
                  setError(null);
                  await signInWithGoogle(credential);
                }}
                onError={setError}
              />
              <div className="mt-6 flex items-center gap-3 text-xs text-faint" aria-hidden>
                <span className="h-px flex-1 bg-line" />
                or with email
                <span className="h-px flex-1 bg-line" />
              </div>
            </div>
          )}

          <div className={`${config?.google_client_id ? "mt-5" : "mt-7"} space-y-4`}>
            {mode === "signup" && (
              <div>
                <label className="label" htmlFor="name">Name</label>
                <input id="name" className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" placeholder="Ada Lovelace" />
              </div>
            )}
            <div>
              <label className="label" htmlFor="email">Email</label>
              <input id="email" type="email" required className="input" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" placeholder="you@company.com" />
            </div>
            <div>
              <label className="label" htmlFor="password">Password</label>
              <input id="password" type="password" required minLength={mode === "signup" ? 8 : undefined} className="input" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "signin" ? "current-password" : "new-password"} placeholder={mode === "signup" ? "At least 8 characters" : ""} />
            </div>
          </div>

          {error && <p className="mt-4 rounded-lg border border-disputed/30 bg-disputed/10 px-3 py-2 text-sm text-disputed">{error}</p>}

          <button className="btn-primary mt-6 w-full py-2.5" disabled={busy}>
            {busy ? <Spinner /> : <>{mode === "signin" ? "Sign in" : "Create account"} <ArrowRight className="h-4 w-4" /></>}
          </button>

          <p className="mt-6 text-center text-sm text-muted">
            {mode === "signin" ? "New to NodexM? " : "Already have an account? "}
            <button type="button" className="font-medium text-accent hover:underline" onClick={() => { setMode(mode === "signin" ? "signup" : "signin"); setError(null); }}>
              {mode === "signin" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </form>
      </section>
    </div>
  );
}
