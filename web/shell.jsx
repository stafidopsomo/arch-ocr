/* App-level shell: chrome, sidebar, screen routing, tweaks panel */

const { useState, useEffect, useRef, useMemo, useCallback } = React;

// ─── Sidebar ──────────────────────────────────────────────────────────
function Sidebar({ screen, onScreen, lang, onLang, onSignOut, user }) {
  const { t } = useT();
  const items = [
    { k: "dashboard", icon: I.layers, label: t("nav_dashboard") },
    { k: "new",       icon: I.plus,   label: t("nav_new") },
    { k: "packets",   icon: I.list,   label: t("nav_packets") },
    { k: "usage",     icon: I.clock,  label: t("nav_usage") },
  ];
  const bottom = [
    { k: "settings", icon: I.user, label: t("nav_settings") },
  ];
  return (
    <aside style={{
      width: 232, flexShrink: 0,
      borderRight: "1px solid var(--hairline)",
      background: "var(--paper)",
      display: "flex", flexDirection: "column",
      padding: "14px 12px",
    }}>
      <div style={{ padding: "6px 8px 18px" }}>
        <BrandLockup />
      </div>
      <nav style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {items.map(it => (
          <SideLink key={it.k} active={screen === it.k} onClick={() => onScreen(it.k)} icon={it.icon} label={it.label} />
        ))}
      </nav>
      <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
        {bottom.map(it => (
          <SideLink key={it.k} active={screen === it.k} onClick={() => onScreen(it.k)} icon={it.icon} label={it.label} />
        ))}
        <div style={{ padding: "10px 8px 4px", borderTop: "1px solid var(--hairline)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 26, height: 26, borderRadius: "50%",
              background: "var(--accent)",
              color: "#fff",
              display: "grid", placeItems: "center",
              fontSize: 11, fontWeight: 600,
            }}>{String(user?.username || "U").slice(0, 2).toUpperCase()}</div>
            <div style={{ fontSize: 12, lineHeight: 1.2 }}>
              <div style={{ fontWeight: 500, color: "var(--ink-1)" }}>{user?.username || "Demo user"}</div>
              <div className="muted" style={{ fontSize: 11 }}>{user?.role || "demo"}</div>
            </div>
          </div>
          <LangSwitch lang={lang} onChange={onLang} />
        </div>
        {onSignOut && (
          <button className="btn btn-ghost btn-sm" onClick={onSignOut} style={{ justifyContent: "center" }}>
            {t("sign_out")}
          </button>
        )}
      </div>
    </aside>
  );
}

function SideLink({ active, icon: Ic, label, onClick }) {
  return (
    <button onClick={onClick} className="row-btn" style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "8px 10px",
      borderRadius: "var(--r-md)",
      color: active ? "var(--ink-1)" : "var(--ink-3)",
      background: active ? "var(--paper-2)" : "transparent",
      fontWeight: active ? 500 : 400,
      fontSize: 13.5,
      width: "100%", textAlign: "left",
      border: "1px solid", borderColor: active ? "var(--hairline)" : "transparent",
    }}>
      <Ic size={16} stroke={1.6} />
      {label}
    </button>
  );
}

// ─── App shell ────────────────────────────────────────────────────────
function AppShell({ children, screen, onScreen, lang, onLang, hideSidebar, onSignOut, user }) {
  if (hideSidebar) return <div style={{ minHeight: "100vh" }}>{children}</div>;
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <Sidebar screen={screen} onScreen={onScreen} lang={lang} onLang={onLang} onSignOut={onSignOut} user={user} />
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>{children}</main>
    </div>
  );
}

// ─── Top bar inside main ──────────────────────────────────────────────
function TopBar({ title, eyebrow, right }) {
  return (
    <div style={{
      display: "flex", alignItems: "flex-end", justifyContent: "space-between",
      gap: 16,
      padding: "26px 36px 18px",
      borderBottom: "1px solid var(--hairline)",
      background: "var(--paper)",
    }}>
      <div>
        {eyebrow && <div className="muted" style={{ fontSize: 12, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4 }}>{eyebrow}</div>}
        <h1 className="serif" style={{ margin: 0, fontSize: 26, fontWeight: 600, color: "var(--ink-1)", letterSpacing: "-0.015em" }}>{title}</h1>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>{right}</div>
    </div>
  );
}

window.AppShell = AppShell;
window.TopBar = TopBar;
