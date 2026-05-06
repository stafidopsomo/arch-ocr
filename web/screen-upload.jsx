/* New packet upload screen */

function UploadScreen({ onStart }) {
  const { t, lang } = useT();
  const [files, setFiles] = useState([]);
  const [title, setTitle] = useState("");
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef(null);

  const filesCount = files.length;
  const sizeMb = (files.reduce((s, f) => s + f.size, 0) / (1024 * 1024)).toFixed(1);

  const overFiles = filesCount > 10;
  const overSize = Number(sizeMb) > 100;
  const canSubmit = filesCount > 0 && !overFiles && !overSize && !submitting;

  function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;
    setError("");
    setFiles((current) => [...current, ...incoming]);
  }
  function remove(i) { setFiles(files.filter((_, idx) => idx !== i)); }
  async function submit() {
    setError("");
    setSubmitting(true);
    try {
      await onStart(files, title);
    } catch (err) {
      setError(err.message || String(err));
      setSubmitting(false);
    }
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      <TopBar
        eyebrow={t("nav_new")}
        title={t("upload_title")}
        right={
          <div style={{ fontSize: 12, color: "var(--ink-4)", display: "flex", alignItems: "center", gap: 8 }}>
            <I.info size={14} />
            <span>{t("upload_processing")}</span>
          </div>
        }
      />

      <div style={{ padding: "28px 36px", maxWidth: 980, width: "100%" }}>
        <div className="muted" style={{ fontSize: 13, marginTop: -6, marginBottom: 24 }}>{t("upload_sub")}</div>

        {/* dropzone */}
        <div
          onDragOver={e => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={e => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
          onClick={() => inputRef.current?.click()}
          style={{
            border: `1.5px dashed ${drag ? "var(--accent)" : "var(--hairline-2)"}`,
            background: drag ? "var(--accent-tint)" : "var(--paper)",
            borderRadius: "var(--r-lg)",
            padding: "44px 24px",
            textAlign: "center",
            cursor: "pointer",
            transition: "background 120ms, border 120ms",
          }}
        >
          <div style={{
            width: 44, height: 44, borderRadius: "50%",
            margin: "0 auto 12px",
            background: "var(--paper-2)",
            border: "1px solid var(--hairline)",
            display: "grid", placeItems: "center",
            color: "var(--accent)",
          }}>
            <I.upload size={20} />
          </div>
          <div className="serif" style={{ fontSize: 17, fontWeight: 500, color: "var(--ink-1)", marginBottom: 4 }}>
            {t("upload_drop")}
          </div>
          <div className="muted" style={{ fontSize: 12.5 }}>{t("upload_hint")}</div>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp"
            onChange={(e) => addFiles(e.target.files)}
            style={{ display: "none" }}
          />
        </div>

        {/* counters */}
        <div style={{ display: "flex", gap: 24, marginTop: 18, fontSize: 13 }}>
          <Counter label={t("upload_files_count", filesCount, 10)} value={filesCount} max={10} over={overFiles} />
          <Counter label={lang === "el" ? "Έως 20 σελίδες θα επιλεγούν από το backend" : "Backend selects up to 20 pages"} value={Math.min(filesCount, 10)} max={10} over={false} />
          <div style={{ marginLeft: "auto", color: "var(--ink-4)", display: "flex", alignItems: "center", gap: 6 }} className="mono">
            {sizeMb} MB
          </div>
        </div>

        {(overFiles || overSize || error) && (
          <div style={{
            marginTop: 14,
            background: "var(--fail-bg)", border: "1px solid var(--fail-bd)",
            color: "var(--fail)", padding: "10px 12px",
            borderRadius: "var(--r-md)", fontSize: 13, display: "flex", alignItems: "flex-start", gap: 8,
          }}>
            <I.alert size={14} />
            <span>{error || (overFiles ? t("upload_limit_files") : "Maximum upload size is 100 MB.")}</span>
          </div>
        )}

        {/* file list */}
        {files.length > 0 && (
          <div style={{ marginTop: 28 }}>
            <div className="label" style={{ marginBottom: 10 }}>{t("upload_files")}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {files.map((f, i) => (
                <FileRow key={`${f.name}-${i}`} f={f} onRemove={() => remove(i)} t={t} />
              ))}
            </div>
          </div>
        )}

        {/* title */}
        <div style={{ marginTop: 28 }}>
          <label className="label">{t("upload_title_label")}</label>
          <input className="input" value={title} onChange={e => setTitle(e.target.value)}
            placeholder={t("upload_title_ph")} />
        </div>

        {/* limits explainer */}
        <div className="card" style={{ marginTop: 28, padding: 16, background: "var(--paper-2)", borderColor: "var(--hairline)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, fontSize: 12 }}>
            <LimitItem label={lang === "el" ? "Μέγιστα αρχεία" : "Max files"} value="10" />
            <LimitItem label={lang === "el" ? "Μέγιστες σελίδες" : "Max pages"} value="20" />
            <LimitItem label={lang === "el" ? "Αρχείο ≤" : "File ≤"} value="100 MB" />
            <LimitItem label={lang === "el" ? "Πάροχος" : "Provider"} value="Gemini 3.1" mono />
          </div>
        </div>

        {/* submit */}
        <div style={{ marginTop: 28, display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button className="btn">{t("cancel")}</button>
          <button className="btn btn-primary" disabled={!canSubmit} onClick={submit}
            style={{ opacity: canSubmit ? 1 : 0.5, pointerEvents: canSubmit ? "auto" : "none" }}>
            {submitting ? (lang === "el" ? "Ανέβασμα…" : "Uploading…") : t("upload_submit")}
            <I.arrow size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

function Counter({ label, value, max, over }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div style={{ flex: "0 0 auto", minWidth: 200 }}>
      <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>{label}</div>
      <div style={{ height: 4, background: "var(--paper-2)", borderRadius: 999, overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`, height: "100%",
          background: over ? "var(--fail)" : "var(--accent)",
          transition: "width 200ms",
        }} />
      </div>
    </div>
  );
}

function FileRow({ f, onRemove, t }) {
  const ext = f.name.toLowerCase().endsWith(".pdf") ? "PDF" : "IMG";
  const sizeMb = (f.size / (1024 * 1024)).toFixed(1);
  return (
    <div className="card" style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 14px",
      borderRadius: "var(--r-md)",
    }}>
      <div style={{
        width: 30, height: 30, borderRadius: 4,
        border: "1px solid var(--hairline)",
        background: "var(--paper-2)",
        display: "grid", placeItems: "center",
        color: "var(--ink-4)",
        fontSize: 9, fontWeight: 600, fontFamily: "var(--font-mono)",
      }}>{ext}</div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, color: "var(--ink-1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontFamily: "var(--font-mono)" }}>
          {f.name}
        </div>
        <div className="muted" style={{ fontSize: 11.5, marginTop: 1 }}>
          {sizeMb} MB
        </div>
      </div>

      <button onClick={onRemove} className="btn btn-ghost btn-sm" style={{ color: "var(--ink-4)" }}>
        <I.x size={13} />
        {t("upload_remove")}
      </button>
    </div>
  );
}

function LimitItem({ label, value, mono }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 11, letterSpacing: "0.04em", textTransform: "uppercase", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 14, color: "var(--ink-1)", fontWeight: 500, fontFamily: mono ? "var(--font-mono)" : "inherit" }}>{value}</div>
    </div>
  );
}

window.UploadScreen = UploadScreen;
