"use client";

/** AI provider settings — paste a key, pick a model, make it live.
 *
 * Exists so a deployment can be pointed at a model without shell access to
 * edit a .env and restart. The key is write-only from here on: the server
 * returns a mask, never the value, so this screen can confirm WHICH key is
 * installed without being able to leak it. */

import { useCallback, useEffect, useState } from "react";

import {
  clearProvider,
  listProviders,
  testProvider,
  updateProvider,
} from "@/lib/api";
import type { ProviderCredential, ProviderTestResult } from "@/lib/types";
import { useRequireRole } from "@/lib/auth";

const PROVIDER_LABELS: Record<string, string> = {
  gemini: "Google Gemini",
  nvidia: "NVIDIA NIM (Kimi K3)",
  openai: "OpenAI",
};

const MODEL_SUGGESTIONS: Record<string, string[]> = {
  gemini: ["gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview"],
  nvidia: ["moonshotai/kimi-k3"],
  openai: ["gpt-4o-mini", "gpt-4o"],
};

const SOURCE_NOTE: Record<string, string> = {
  database: "Saved here in the dashboard",
  environment: "From the server environment (.env)",
  none: "Not configured",
};

export default function ProviderSettingsPage() {
  const { ready } = useRequireRole(["SUPER_ADMIN"]);
  const [providers, setProviders] = useState<ProviderCredential[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setProviders(await listProviders());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load providers.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (ready) void refresh();
  }, [ready, refresh]);

  if (!ready) return null;

  return (
    <div className="space-y-6">
      <header className="max-w-3xl">
        <h1 className="text-2xl font-semibold tracking-tight text-white">
          AI provider settings
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-mute">
          Paste an API key and choose a model. Changes apply to the next
          inference — no restart. A key saved here overrides the server
          environment; clearing it falls back to the environment.
        </p>
        <p className="mt-2 text-xs leading-relaxed text-mute">
          Keys are encrypted before they are stored and are never sent back to
          this page — you will only ever see the last four characters.
        </p>
      </header>

      {error ? (
        <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </p>
      ) : null}

      {loading ? (
        <p className="text-sm text-mute">Loading…</p>
      ) : (
        <div className="space-y-4">
          {providers.map((provider) => (
            <ProviderCard
              key={provider.provider}
              provider={provider}
              onChanged={refresh}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ProviderCard({
  provider,
  onChanged,
}: {
  provider: ProviderCredential;
  onChanged: () => Promise<void>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(provider.model);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ProviderTestResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setMessage(null);
    setResult(null);
    try {
      await fn();
      await onChanged();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  }

  const save = () =>
    run("save", async () => {
      // Omitting api_key entirely leaves the stored one alone, so the model can
      // be changed without re-pasting a key nobody can read back.
      await updateProvider(provider.provider, {
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        model: model.trim() || undefined,
      });
      setApiKey("");
      setMessage("Saved.");
    });

  const makeLive = () =>
    run("activate", async () => {
      await updateProvider(provider.provider, { make_active: true });
      setMessage("This provider now serves inference.");
    });

  const clear = () =>
    run("clear", async () => {
      await clearProvider(provider.provider);
      setApiKey("");
      setMessage("Cleared. Falling back to the server environment.");
    });

  const test = async () => {
    setBusy("test");
    setMessage(null);
    try {
      setResult(await testProvider(provider.provider));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Test failed to run.");
    } finally {
      setBusy(null);
    }
  };

  const suggestions = MODEL_SUGGESTIONS[provider.provider] ?? [];

  return (
    <section className="rounded-xl border border-white/10 bg-white/5 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-white">
            {PROVIDER_LABELS[provider.provider] ?? provider.provider}
          </h2>
          <p className="mt-1 text-xs text-mute">
            {SOURCE_NOTE[provider.source] ?? provider.source}
            {provider.masked_key ? ` · key ${provider.masked_key}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {provider.is_active ? (
            <span className="rounded-full bg-emerald-500/15 px-3 py-1 text-xs font-medium text-emerald-300">
              Live
            </span>
          ) : (
            <button
              type="button"
              onClick={makeLive}
              disabled={!provider.configured || busy !== null}
              className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white disabled:opacity-40"
            >
              {busy === "activate" ? "Switching…" : "Make live"}
            </button>
          )}
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="text-mute">API key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={
              provider.configured ? "Leave blank to keep the saved key" : "Paste the key"
            }
            autoComplete="off"
            className="mt-1 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-white placeholder:text-mute/60"
          />
        </label>

        <label className="block text-sm">
          <span className="text-mute">Model</span>
          <input
            value={model}
            onChange={(event) => setModel(event.target.value)}
            list={`models-${provider.provider}`}
            className="mt-1 w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-white"
          />
          <datalist id={`models-${provider.provider}`}>
            {suggestions.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </label>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={save}
          disabled={busy !== null}
          className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black disabled:opacity-40"
        >
          {busy === "save" ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={test}
          disabled={busy !== null || !provider.configured}
          className="rounded-lg border border-white/15 px-4 py-2 text-sm text-white disabled:opacity-40"
          title="Makes one real call to confirm the key works"
        >
          {busy === "test" ? "Testing…" : "Test key"}
        </button>
        {provider.source === "database" ? (
          <button
            type="button"
            onClick={clear}
            disabled={busy !== null}
            className="rounded-lg border border-red-500/30 px-4 py-2 text-sm text-red-200 disabled:opacity-40"
          >
            {busy === "clear" ? "Clearing…" : "Clear saved key"}
          </button>
        ) : null}
      </div>

      {message ? <p className="mt-3 text-sm text-mute">{message}</p> : null}

      {result ? (
        <p
          className={`mt-3 rounded-lg px-3 py-2 text-sm ${
            result.ok
              ? "bg-emerald-500/10 text-emerald-200"
              : "bg-red-500/10 text-red-200"
          }`}
        >
          {result.ok
            ? `Working — ${result.model} answered in ${result.latency_ms ?? "?"}ms.`
            : `Failed (${result.error_kind ?? "error"})${
                result.detail ? ` — ${result.detail}` : ""
              }`}
        </p>
      ) : null}
    </section>
  );
}
