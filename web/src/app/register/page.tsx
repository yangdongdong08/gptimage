"use client";

import { useEffect, useRef } from "react";
import { LoaderCircle } from "lucide-react";

import webConfig from "@/constants/common-env";
import { useAuthGuard } from "@/lib/use-auth-guard";
import type { RegisterConfig } from "@/lib/api";
import { getStoredAuthKey } from "@/store/auth";

import { useSettingsStore } from "../settings/store";
import { RegisterCard } from "./components/register-card";

function RegisterDataController() {
  const didLoadRef = useRef(false);
  const loadRegister = useSettingsStore((state) => state.loadRegister);
  const setRegisterConfig = useSettingsStore((state) => state.setRegisterConfig);

  useEffect(() => {
    if (didLoadRef.current) return;
    didLoadRef.current = true;
    void loadRegister();
  }, [loadRegister]);

  useEffect(() => {
    let closed = false;
    const controller = new AbortController();

    const waitBeforeRetry = () => new Promise((resolve) => window.setTimeout(resolve, 1500));
    const connect = async (token: string) => {
      const baseUrl = webConfig.apiUrl.replace(/\/$/, "");
      const response = await fetch(`${baseUrl}/api/register/events`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`register_events_http_${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split(/\r?\n\r?\n/);
        buffer = events.pop() || "";
        for (const event of events) {
          const data = event
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (!data) continue;
          try {
            setRegisterConfig(JSON.parse(data) as RegisterConfig);
          } catch {
            // Ignore an incomplete event and keep the stream alive.
          }
        }
      }
    };

    void (async () => {
      const token = await getStoredAuthKey();
      if (!token) return;
      while (!closed) {
        try {
          await connect(token);
        } catch {
          if (closed || controller.signal.aborted) return;
        }
        if (!closed) await waitBeforeRetry();
      }
    })();

    return () => {
      closed = true;
      controller.abort();
    };
  }, [setRegisterConfig]);

  return null;
}

function RegisterPageContent() {
  return (
    <>
      <RegisterDataController />
      <section className="mb-2 flex flex-col gap-1 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Register</div>
          <h1 className="text-2xl font-semibold tracking-tight">ChatGPT注册机</h1>
        </div>
      </section>
      <section>
        <RegisterCard />
      </section>
    </>
  );
}

export default function RegisterPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <RegisterPageContent />;
}
