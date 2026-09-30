// Thin wrapper over the portal's existing FastAPI routes. The backend is
// unchanged - this rewrite is UI only - so every path here already exists.

export type Agent = {
  name: string;
  id: string | null;
  task: string;
  kind: string;
  kind_label: string;
  kind_hint: string;
  display_name: string;
  blurb: string;
  ready: boolean;
  state: string;
  upload_volume: string;
  output_volume: string;
  accepts: string[];
  supports_files: boolean;
  access_reason?: string;
  metered?: boolean;
  light?: boolean;
};

export type Session = {
  user_name: string;
  display_name: string;
  groups: string[];
  is_admin: boolean;
  auth_mode: string;
};

export type Grant = {
  principal: string;
  kind: string;
  level: string;
  inherited: boolean;
};

export type AdminAgent = Agent & {
  grants: Grant[];
  acl_error: string | null;
  manageable: boolean;
  you_can_manage?: boolean;
  agent_id?: string;
};

export type Reply = {
  reply: string;
  tools: string[];
  citations: { label: string; url: string }[];
  attachments: { name: string; path: string }[];
  warning?: string;
};

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
  );
  const text = await res.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!res.ok) throw new Error(data.error || data.detail || `Request failed (${res.status})`);
  return data as T;
}

export const api = {
  session: () => request<Session>("/api/session"),
  agents: () => request<{ agents: Agent[] }>("/api/agents"),
  models: () =>
    request<{ enabled: boolean; allowed: boolean; reason: string; models: Agent[] }>("/api/models"),
  chat: (endpoint: string, history: { role: string; content: string }[], files: string[]) =>
    request<Reply>("/api/chat", { endpoint, history, files }),
  adminOverview: () =>
    request<{
      agents: AdminAgent[];
      groups: { name: string; id: string; local: boolean }[];
      users: { name: string; display: string }[];
    }>("/api/admin/overview"),
  grant: (endpoint_id: string, kind: string, principal: string, level: string | null) =>
    request<{ access_control_list: unknown[]; warning?: string }>("/api/admin/grant", {
      endpoint_id,
      kind,
      principal,
      level,
    }),
  meta: (payload: Record<string, string>) => request<unknown>("/api/admin/meta", payload),
  llmState: () =>
    request<{
      enabled: boolean;
      group: string;
      group_id: string | null;
      members: string[];
      members_visible?: boolean;
    }>("/api/admin/llm"),
  llmSet: (payload: Record<string, unknown>) => request<unknown>("/api/admin/llm", payload),
  cost: (days: number) =>
    request<{
      days: number;
      available: boolean;
      note: string;
      total_usd: number | null;
      lines: { sku: string; dbus: number; usd: number | null }[];
    }>(`/api/admin/cost?days=${days}`),
};

export async function upload(endpoint: string, file: File) {
  const fd = new FormData();
  fd.append("endpoint", endpoint);
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const text = await res.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!res.ok) throw new Error(data.error || data.detail || "Upload failed");
  return data as { path: string; name: string; bytes: number };
}

// A plain GET link, not a fetch wrapper: letting the browser navigate lets it
// honour the backend's Content-Disposition header and show its own native
// download UI, rather than the app re-implementing a save-file flow.
export function downloadUrl(endpoint: string, path: string) {
  return "/api/download?" + new URLSearchParams({ endpoint, path }).toString();
}
