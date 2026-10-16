import { useEffect, useState } from "react";
import { request } from "../api";
import { useT } from "../i18n";

type Inventory = {
  mode: "offline" | "live";
  checks: Array<{ id: string; status: string }>;
  usage_limits?: {
    configuration_required: boolean;
    charged_upper_cny: number;
    unsettled_reserved_cny: number;
    unknown_requests: number;
  } | null;
};

const labels: Record<string, string> = {
  chat: "Chat", goals: "Goals", reminders: "Reminders", memory: "Memory",
  files: "Files", browser: "Browser", computer: "Computer control", research: "Research",
  usage_limits: "Local model limits",
};
const statuses: Record<string, string> = {
  registered: "Tool registered; execution not verified",
  configured: "Model configured; connection not verified",
  needs_configuration: "Needs model configuration",
  missing_dependency: "Browser dependency missing",
  disabled: "Disabled", offline: "Offline scripted responses",
  needs_budget_configuration: "Set a local allowance before live model requests",
  limits_configured: "Local limits configured",
};

export function AssistantCapabilities({ revision }: { revision: number }) {
  const t = useT();
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    request<Inventory>("/api/assistant/capabilities").then((value) => {
      if (active) { setInventory(value); setFailed(false); }
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [revision]);
  return (
    <section className="rounded-2xl border border-line p-4" aria-label={t("Assistant capabilities")}>
      <h2 className="font-medium">{t("Assistant capabilities")}</h2>
      <p className="mt-1 text-sm text-muted">{t("Registration does not verify browser, desktop or model execution.")}</p>
      {failed ? <p role="alert">{t("Could not load assistant capabilities.")}</p> : !inventory ?
        <p role="status">{t("Loading…")}</p> : <>
          {inventory.mode === "offline" && <p className="mt-2 text-sm">{t("Offline scripted responses")}</p>}
          <dl className="mt-3 space-y-2 text-sm">
            {inventory.checks.map((check) => <div key={check.id} className="flex flex-wrap justify-between gap-2">
              <dt>{t(labels[check.id] ?? check.id)}</dt>
              <dd className="text-muted">{t(statuses[check.status] ?? check.status)}</dd>
            </div>)}
          </dl>
          {inventory.usage_limits && <div className="mt-3 border-t border-line pt-3 text-sm">
            <p>{t("Known cost upper bound (CNY)")}: {inventory.usage_limits.charged_upper_cny.toFixed(6)}</p>
            <p>{t("Unresolved reservations (CNY)")}: {inventory.usage_limits.unsettled_reserved_cny.toFixed(6)}</p>
            <p>{t("Requests needing reconciliation")}: {inventory.usage_limits.unknown_requests}</p>
            <p className="mt-1 text-muted">{t("These estimates do not verify the provider bill. Limits are configured in the local startup file.")}</p>
          </div>}
        </>}
    </section>
  );
}
