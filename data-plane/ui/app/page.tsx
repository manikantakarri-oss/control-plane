"use client";

import { useEffect, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { Agent, api, Session } from "@/lib/api";
import { AgentCard } from "@/components/AgentCard";
import { ModelChoice } from "@/components/ModelChoice";
import { Chat } from "@/components/Chat";
import { Admin } from "@/components/Admin";
import { Empty, ErrorBox, SectionHead, Spinner } from "@/components/bits";
import { ThemeToggle } from "@/components/ThemeToggle";

export default function Page() {
  const [session, setSession] = useState<Session | null>(null);
  const [fatal, setFatal] = useState("");

  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [agentsErr, setAgentsErr] = useState("");

  const [models, setModels] = useState<Agent[] | null>(null);
  const [modelsNote, setModelsNote] = useState("");
  const [modelsOffered, setModelsOffered] = useState(false);

  const [open, setOpen] = useState<Agent | null>(null);
  const [tab, setTab] = useState("agents");

  // Tabs are reflected in the URL so a section can be linked to and survives
  // a refresh.
  useEffect(() => {
    const want = window.location.hash.replace("#", "");
    if (want === "models" || want === "admin") setTab(want);
  }, []);

  function pickTab(next: string) {
    setTab(next);
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", next === "agents" ? "#" : "#" + next);
    }
  }

  useEffect(() => {
    api
      .session()
      .then(setSession)
      .catch((e) => setFatal(e.message));
  }, []);

  useEffect(() => {
    if (!session) return;
    api
      .agents()
      .then((d) => setAgents(d.agents))
      .catch((e) => setAgentsErr(e.message));
    api
      .models()
      .then((d) => {
        setModelsOffered(d.allowed);
        setModels(d.models);
        setModelsNote(d.reason || "");
      })
      .catch(() => setModelsOffered(false));
  }, [session]);

  if (fatal) {
    return (
      <Main>
        <div className="mt-10">
          <ErrorBox>{fatal}</ErrorBox>
        </div>
      </Main>
    );
  }

  if (!session) {
    return (
      <Main>
        <div className="mt-10">
          <Spinner label="Signing you in…" />
        </div>
      </Main>
    );
  }

  return (
    <>
      <header
        className="sticky top-0 z-10 flex flex-wrap items-center gap-3 px-5 py-3.5"
        style={{ background: "var(--surface)", borderBottom: "1px solid var(--line)" }}
      >
        <span className="text-[17px] font-semibold tracking-[-0.01em]">Agent Portal</span>
        <span className="tag">{session.display_name}</span>
        <span className="flex-1" />
        <span className="hidden text-xs faint sm:inline">
          {session.auth_mode === "local-dev" ? "local development" : "signed in via Databricks"}
        </span>
        <ThemeToggle />
      </header>

      <Main>
        {open ? (
          <Chat
            agent={open}
            agents={agents || []}
            models={models || []}
            onBack={() => setOpen(null)}
            onSwitch={setOpen}
          />
        ) : (
          <Tabs.Root value={tab} onValueChange={pickTab}>
            <Tabs.List
              className="mb-6 flex gap-1 overflow-x-auto"
              aria-label="Sections"
            >
              <TabButton value="agents">Your agents</TabButton>
              {modelsOffered ? (
                <TabButton value="models">
                  Ask an assistant
                </TabButton>
              ) : null}
              {session.is_admin ? <TabButton value="admin">Manage access</TabButton> : null}
            </Tabs.List>

            <Tabs.Content value="agents">
              <SectionHead title="Your agents">
                Assistants built for your team. Pick one and ask it a question in plain English.
              </SectionHead>
              <ErrorBox>{agentsErr}</ErrorBox>
              {agents === null ? (
                <Spinner label="Loading your agents…" />
              ) : agents.length === 0 ? (
                <Empty
                  title="You do not have any agents yet"
                  hint="An admin needs to give you access before anything appears here."
                />
              ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {agents.map((a) => (
                    <AgentCard key={a.name} agent={a} onOpen={setOpen} />
                  ))}
                </div>
              )}
            </Tabs.Content>

            {modelsOffered ? (
              <Tabs.Content value="models">
                {models === null ? (
                  <Spinner label="Loading…" />
                ) : models.length === 0 ? (
                  <Empty title="No models are available to you" hint={modelsNote} />
                ) : (
                  <ModelChoice models={models} onOpen={setOpen} />
                )}
              </Tabs.Content>
            ) : null}

            {session.is_admin ? (
              <Tabs.Content value="admin">
                <Admin />
              </Tabs.Content>
            ) : null}
          </Tabs.Root>
        )}
      </Main>
    </>
  );
}

function TabButton({ value, children }: { value: string; children: React.ReactNode }) {
  return (
    <Tabs.Trigger value={value} className="tab">
      {children}
    </Tabs.Trigger>
  );
}

function Main({ children }: { children: React.ReactNode }) {
  return <main className="mx-auto w-full max-w-5xl px-5 pb-20 pt-6">{children}</main>;
}
