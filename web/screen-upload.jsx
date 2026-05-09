/* New packet upload screen */

function UploadScreen({ token, limits, onStart }) {
  const { t, lang } = useT();
  const [files, setFiles] = useState([]);
  const [title, setTitle] = useState("");
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({});
  const [totalUploadProgress, setTotalUploadProgress] = useState(0);
  const [draftJob, setDraftJob] = useState(null);
  const inputRef = useRef(null);
  const folderInputRef = useRef(null);

  const filesCount = files.length;
  const sizeMb = (files.reduce((s, f) => s + f.size, 0) / (1024 * 1024)).toFixed(1);

  const maxFiles = Number(limits?.max_files_per_packet || 10);
  const maxPages = Number(limits?.max_pages_per_packet || 40);
  const maxUploadMb = Number(limits?.max_upload_mb || 100);
  const overFiles = filesCount > maxFiles;
  const overSize = Number(sizeMb) > maxUploadMb;
  const allUploaded = filesCount > 0 && draftJob && !uploading;
  const canSubmit = allUploaded && !overFiles && !overSize && !submitting;

  function relativeName(file) {
    return file.webkitRelativePath || file.relativePath || file.name;
  }

  function supportedFile(file) {
    return /\.(pdf|png|jpe?g|tiff?|webp)$/i.test(file.name || "");
  }

  async function filesFromDataTransfer(dataTransfer) {
    const items = Array.from(dataTransfer?.items || []);
    const entries = items
      .map((item) => item.webkitGetAsEntry?.())
      .filter(Boolean);
    if (!entries.length) return Array.from(dataTransfer?.files || []).filter(supportedFile);

    async function walk(entry, prefix = "") {
      if (entry.isFile) {
        return new Promise((resolve) => {
          entry.file((file) => {
            file.relativePath = `${prefix}${file.name}`;
            resolve(supportedFile(file) ? [file] : []);
          }, () => resolve([]));
        });
      }
      if (!entry.isDirectory) return [];
      const reader = entry.createReader();
      const children = [];
      while (true) {
        const batch = await new Promise((resolve) => reader.readEntries(resolve));
        if (!batch.length) break;
        children.push(...batch);
      }
      const nested = await Promise.all(children.map((child) => walk(child, `${prefix}${entry.name}/`)));
      return nested.flat();
    }

    const nested = await Promise.all(entries.map((entry) => walk(entry)));
    return nested.flat();
  }

  function addFiles(fileList) {
    const incoming = Array.from(fileList || []).filter(supportedFile);
    if (!incoming.length) return;
    setError("");
    const nextFiles = [...files, ...incoming];
    setFiles(nextFiles);
    setDraftJob(null);
    setTotalUploadProgress(0);
    uploadDraft(nextFiles);
  }
  function remove(i) {
    const nextFiles = files.filter((_, idx) => idx !== i);
    setFiles(nextFiles);
    setDraftJob(null);
    setUploadProgress({});
    setTotalUploadProgress(0);
    if (nextFiles.length) uploadDraft(nextFiles);
  }
  function uploadDraft(nextFiles) {
    setUploading(true);
    setError("");
    setTotalUploadProgress(0);
    setUploadProgress(Object.fromEntries(nextFiles.map((file, index) => [index, 0])));
    const form = new FormData();
    nextFiles.forEach((file) => form.append("files", file, relativeName(file)));
    if (title) form.append("title", title);
    if (token) form.append("token", token);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/jobs/draft");
    xhr.setRequestHeader("accept", "application/json");
    if (token) xhr.setRequestHeader("x-admin-token", token);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const pct = Math.max(1, Math.round((event.loaded / event.total) * 100));
      setTotalUploadProgress(pct);
    };
    xhr.onload = () => {
      setUploading(false);
      const data = JSON.parse(xhr.responseText || "{}");
      if (xhr.status < 200 || xhr.status >= 300) {
        setError(data.detail || `Upload failed (${xhr.status})`);
        return;
      }
      setDraftJob(data);
      setTotalUploadProgress(100);
      setUploadProgress(Object.fromEntries(nextFiles.map((file, index) => [index, 100])));
    };
    xhr.onerror = () => {
      setUploading(false);
      setError("Upload failed.");
    };
    xhr.send(form);
  }
  async function submit() {
    setError("");
    setSubmitting(true);
    try {
      const res = await fetch(`/jobs/${encodeURIComponent(draftJob.job_id)}/start`, {
        method: "POST",
        headers: { "content-type": "application/json", "accept": "application/json", ...(token ? { "x-admin-token": token } : {}) },
        credentials: "same-origin",
        body: JSON.stringify({ title }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Start failed (${res.status})`);
      await onStart([], title, data);
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
          onDrop={async e => { e.preventDefault(); setDrag(false); addFiles(await filesFromDataTransfer(e.dataTransfer)); }}
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
          <div style={{ display: "flex", justifyContent: "center", gap: 10, marginTop: 16 }}>
            <button type="button" className="btn" onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}>
              {lang === "el" ? "Επιλογή αρχείων" : "Choose files"}
            </button>
            <button type="button" className="btn btn-primary" onClick={(e) => { e.stopPropagation(); folderInputRef.current?.click(); }}>
              {lang === "el" ? "Επιλογή φακέλου" : "Choose folder"}
            </button>
          </div>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp"
            onChange={(e) => addFiles(e.target.files)}
            style={{ display: "none" }}
          />
          <input
            ref={folderInputRef}
            type="file"
            multiple
            webkitdirectory=""
            directory=""
            onChange={(e) => addFiles(e.target.files)}
            style={{ display: "none" }}
          />
        </div>

        {/* counters */}
        <div style={{ display: "flex", gap: 24, marginTop: 18, fontSize: 13 }}>
          <Counter label={t("upload_files_count", filesCount, maxFiles)} value={filesCount} max={maxFiles} over={overFiles} />
          <Counter label={lang === "el" ? `Έως ${maxPages} σελίδες θα επιλεγούν από το backend` : `Backend selects up to ${maxPages} pages`} value={Math.min(filesCount, maxFiles)} max={maxFiles} over={false} />
          <div style={{ marginLeft: "auto", color: "var(--ink-4)", display: "flex", alignItems: "center", gap: 6 }} className="mono">
            {sizeMb} MB
          </div>
        </div>

        {files.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 5 }}>
              <div className="muted" style={{ fontSize: 12 }}>
                {uploading
                  ? (lang === "el" ? "Συνολικό ανέβασμα" : "Total upload")
                  : draftJob
                  ? (lang === "el" ? "Ανέβηκαν όλα τα αρχεία" : "All files uploaded")
                  : (lang === "el" ? "Αρχεία σε αναμονή" : "Files pending")}
              </div>
              <div className="mono muted" style={{ fontSize: 11 }}>{Math.round(totalUploadProgress)}%</div>
            </div>
            <div style={{ height: 5, background: "var(--paper-2)", borderRadius: 999, overflow: "hidden", border: "1px solid var(--hairline)" }}>
              <div style={{
                width: `${Math.round(totalUploadProgress)}%`,
                height: "100%",
                background: totalUploadProgress >= 100 ? "var(--pass)" : "var(--accent)",
                transition: "width 160ms",
              }} />
            </div>
          </div>
        )}

        {(overFiles || overSize || error) && (
          <div style={{
            marginTop: 14,
            background: "var(--fail-bg)", border: "1px solid var(--fail-bd)",
            color: "var(--fail)", padding: "10px 12px",
            borderRadius: "var(--r-md)", fontSize: 13, display: "flex", alignItems: "flex-start", gap: 8,
          }}>
            <I.alert size={14} />
            <span>{error || (overFiles ? (lang === "el" ? `Μέγιστο ${maxFiles} αρχεία ανά φάκελο.` : `Maximum ${maxFiles} files per packet.`) : `Maximum upload size is ${maxUploadMb} MB.`)}</span>
          </div>
        )}

        {/* file list */}
        {files.length > 0 && (
          <div style={{ marginTop: 28 }}>
            <div className="label" style={{ marginBottom: 10 }}>{t("upload_files")}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {files.map((f, i) => (
                <FileRow key={`${f.name}-${i}`} f={f} progress={uploadProgress[i] || 0} uploading={uploading} onRemove={() => remove(i)} t={t} lang={lang} />
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
            <LimitItem label={lang === "el" ? "Μέγιστα αρχεία" : "Max files"} value={String(maxFiles)} />
            <LimitItem label={lang === "el" ? "Μέγιστες σελίδες" : "Max pages"} value={String(maxPages)} />
            <LimitItem label={lang === "el" ? "Αρχείο ≤" : "File ≤"} value={`${maxUploadMb} MB`} />
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

function FileRow({ f, progress, uploading, onRemove, t, lang }) {
  const ext = f.name.toLowerCase().endsWith(".pdf") ? "PDF" : "IMG";
  const sizeMb = (f.size / (1024 * 1024)).toFixed(1);
  const displayName = f.webkitRelativePath || f.relativePath || f.name;
  const complete = progress >= 100;
  const state = complete
    ? (lang === "el" ? "ανέβηκε" : "uploaded")
    : uploading
    ? (lang === "el" ? "στο batch ανεβάσματος" : "uploading batch")
    : (lang === "el" ? "αναμονή" : "pending");
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
          {displayName}
        </div>
        <div className="muted" style={{ fontSize: 11.5, marginTop: 1 }}>
          {sizeMb} MB · {state}
        </div>
        <div style={{ height: 4, background: "var(--paper-2)", borderRadius: 999, overflow: "hidden", marginTop: 6 }}>
          <div style={{ width: complete ? "100%" : "0%", height: "100%", background: complete ? "var(--pass)" : "var(--accent)", transition: "width 160ms" }} />
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
