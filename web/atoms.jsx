/* Reusable atoms: icons, badges, status pills */

// minimal lucide-style icons (24x24 stroke)
const Icon = ({ d, size = 16, stroke = 1.6, ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" {...rest}>
    {Array.isArray(d) ? d.map((p, i) => <path key={i} d={p} />) : <path d={d} />}
  </svg>
);

const I = {
  upload: (p) => <Icon {...p} d="M12 3v12 M7 8l5-5 5 5 M5 21h14" />,
  file:   (p) => <Icon {...p} d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5" />,
  filePdf:(p) => <Icon {...p} d={["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z","M14 3v5h5","M9 14h2 M9 17h6 M13 14h2"]} />,
  x:      (p) => <Icon {...p} d="M6 6l12 12 M6 18L18 6" />,
  check:  (p) => <Icon {...p} d="M5 12l4 4 10-10" />,
  alert:  (p) => <Icon {...p} d={["M12 9v4","M12 17h.01","M10.3 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"]} />,
  info:   (p) => <Icon {...p} d={["M12 16v-4","M12 8h.01","M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z"]} />,
  spin:   (p) => <Icon {...p} d="M21 12a9 9 0 1 1-6.2-8.55" />,
  arrow:  (p) => <Icon {...p} d="M5 12h14 M13 5l7 7-7 7" />,
  arrowL: (p) => <Icon {...p} d="M19 12H5 M11 5l-7 7 7 7" />,
  copy:   (p) => <Icon {...p} d={["M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2z","M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"]} />,
  download: (p) => <Icon {...p} d="M12 3v12 M7 10l5 5 5-5 M5 21h14" />,
  search: (p) => <Icon {...p} d={["M21 21l-4.3-4.3","M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z"]} />,
  globe:  (p) => <Icon {...p} d={["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z","M2 12h20","M12 2a15 15 0 0 1 0 20 M12 2a15 15 0 0 0 0 20"]} />,
  plus:   (p) => <Icon {...p} d="M12 5v14 M5 12h14" />,
  list:   (p) => <Icon {...p} d="M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01" />,
  layers: (p) => <Icon {...p} d={["M12 2 2 7l10 5 10-5-10-5z","M2 17l10 5 10-5","M2 12l10 5 10-5"]} />,
  hand:   (p) => <Icon {...p} d="M9 11V6a2 2 0 0 1 4 0v5 M13 11V4a2 2 0 0 1 4 0v9 M17 13V8a2 2 0 0 1 4 0v8a6 6 0 0 1-6 6h-2a8 8 0 0 1-8-8v-1a2 2 0 0 1 4 0v3" />,
  stamp:  (p) => <Icon {...p} d={["M5 21h14","M7 17a5 5 0 0 1 0-10h.5L9 4h6l1.5 3H17a5 5 0 0 1 0 10H7z"]} />,
  pen:    (p) => <Icon {...p} d="M17 3a2.83 2.83 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5z" />,
  lock:   (p) => <Icon {...p} d={["M5 11h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1z","M8 11V7a4 4 0 0 1 8 0v4"]} />,
  eye:    (p) => <Icon {...p} d={["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z","M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"]} />,
  eyeOff: (p) => <Icon {...p} d={["M3 3l18 18","M10.6 10.6A3 3 0 0 0 13.4 13.4","M9.9 4.2A10.5 10.5 0 0 1 12 4c6.5 0 10 8 10 8a18.4 18.4 0 0 1-3.2 4.3","M6.5 6.5C3.7 8.4 2 12 2 12s3.5 8 10 8c1.3 0 2.5-.3 3.6-.8"]} />,
  user:   (p) => <Icon {...p} d={["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z","M4 21a8 8 0 0 1 16 0"]} />,
  clock:  (p) => <Icon {...p} d={["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z","M12 6v6l4 2"]} />,
  chev:   (p) => <Icon {...p} d="M9 6l6 6-6 6" />,
};

// Status pill that maps backend statuses to badge classes
function StatusPill({ status, size = "md", children }) {
  const map = {
    pass: "badge-pass", warning: "badge-warn", fail: "badge-fail", unknown: "badge-unk",
    needs_review: "badge-warn",
    queued: "badge-unk", triaging: "badge-accent",
    processing: "badge-accent", throttled: "badge-warn", retrying: "badge-warn",
    rate_limited: "badge-warn", completed: "badge-pass",
    completed_with_errors: "badge-warn", failed: "badge-fail",
  };
  const cls = map[status] || "badge-unk";
  return (
    <span className={`badge ${cls}`} style={size === "sm" ? { height: 20, fontSize: 11 } : null}>
      <span className="dot" />
      {children}
    </span>
  );
}

function ARCHLogo({ size = 22 }) {
  // Custom mark: stylized A formed by two beams (like architectural elevation lines)
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M3 21 L12 3 L21 21" stroke="var(--accent)" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M7 14 L17 14" stroke="var(--accent)" strokeWidth="1.6" />
      <circle cx="12" cy="3" r="1.4" fill="var(--accent)" />
    </svg>
  );
}

function BrandLockup() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
      <ARCHLogo size={22} />
      <span style={{
        fontFamily: "var(--font-display)",
        fontWeight: 600, fontSize: 16, letterSpacing: "-0.01em",
        color: "var(--ink-1)",
      }}>arch-ocr</span>
    </div>
  );
}

// Language switcher
function LangSwitch({ lang, onChange }) {
  return (
    <div style={{
      display: "inline-flex",
      border: "1px solid var(--hairline-2)",
      borderRadius: "var(--r-md)",
      padding: 2,
      background: "var(--paper)",
      fontSize: 12,
    }}>
      {["en", "el"].map(l => (
        <button key={l}
          onClick={() => onChange(l)}
          style={{
            padding: "4px 10px",
            border: 0,
            borderRadius: 4,
            background: lang === l ? "var(--accent)" : "transparent",
            color: lang === l ? "#fff" : "var(--ink-3)",
            fontWeight: 500,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}>
          {l}
        </button>
      ))}
    </div>
  );
}

window.I = I;
window.Icon = Icon;
window.StatusPill = StatusPill;
window.ARCHLogo = ARCHLogo;
window.BrandLockup = BrandLockup;
window.LangSwitch = LangSwitch;
