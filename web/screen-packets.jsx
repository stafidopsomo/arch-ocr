/* Dashboard / packets list for the connected demo */

function PacketsScreen({ token, mode = "packets", onOpenJob, onNewPacket, onDeleteJob }) {
  const { t, lang } = useT();
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyJob, setBusyJob] = useState("");

  useEffect(() => {
    let stopped = false;
    async function load() {
      try {
        const res = await fetch(`/jobs`, {
          headers: { "accept": "application/json", ...(token ? { "x-admin-token": token } : {}) },
          credentials: "same-origin",
        });
        if (!res.ok) throw new Error(`Jobs failed (${res.status})`);
        const data = await res.json();
        if (!stopped) {
          setJobs(data.jobs || []);
          setError("");
          setLoading(false);
        }
      } catch (err) {
        if (!stopped) {
          setError(err.message || String(err));
          setLoading(false);
        }
      }
    }
    load();
    const id = setInterval(load, 5000);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [token]);

  const totalPackets = jobs.length;
  const activeJobs = jobs.filter((job) => !["completed", "completed_with_errors", "failed", "aborted"].includes(job.status)).length;
  const pagesProcessed = jobs.reduce((sum, job) => sum + Number(job.pages_processed || job.totals?.pages_extracted || 0), 0);
  const estimatedCost = jobs.reduce((sum, job) => sum + Number(job.cost_summary?.estimated_cost_usd || 0), 0);
  const title = mode === "dashboard"
    ? (lang === "el" ? "Πίνακας" : "Dashboard")
    : t("nav_packets");

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      <TopBar
        eyebrow="arch-ocr"
        title={title}
        right={
          <button className="btn btn-primary" onClick={onNewPacket}>
            <I.plus size={14} />
            {t("nav_new")}
          </button>
        }
      />

      <div style={{ padding: "28px 36px", maxWidth: 1180, width: "100%" }}>
        {mode === "dashboard" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12, marginBottom: 24 }}>
            <StatCard label={lang === "el" ? "Φάκελοι" : "Packets" } value={totalPackets} />
            <StatCard label={lang === "el" ? "Ενεργά jobs" : "Active jobs"} value={activeJobs} />
            <StatCard label={lang === "el" ? "Σελίδες" : "Pages processed"} value={pagesProcessed} />
            <StatCard label={lang === "el" ? "Εκτ. κόστος" : "Estimated spend"} value={`$${estimatedCost.toFixed(5)}`} mono />
          </div>
        )}

        {error && (
          <div className="card" style={{ padding: 14, color: "var(--fail)", background: "var(--fail-bg)", borderColor: "var(--fail-bd)", marginBottom: 16 }}>
            {error}
          </div>
        )}

        <div className="card" style={{ overflow: "hidden" }}>
          <div style={{ padding: "16px 18px", borderBottom: "1px solid var(--hairline)", display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div className="label" style={{ margin: 0 }}>{mode === "dashboard" ? (lang === "el" ? "Πρόσφατοι φάκελοι" : "Recent packets") : t("nav_packets")}</div>
            <div className="muted" style={{ fontSize: 12 }}>{loading ? (lang === "el" ? "Φόρτωση…" : "Loading…") : `${jobs.length} ${t("files")}`}</div>
          </div>

          {jobs.length === 0 && !loading ? (
            <div style={{ padding: 28, textAlign: "center" }}>
              <div className="serif" style={{ fontSize: 20, color: "var(--ink-1)", marginBottom: 6 }}>
                {lang === "el" ? "Δεν υπάρχουν jobs ακόμη" : "No jobs yet"}
              </div>
              <div className="muted" style={{ marginBottom: 16 }}>
                {lang === "el" ? "Ανέβασε έναν φάκελο για να εμφανιστεί εδώ." : "Upload a packet and it will appear here."}
              </div>
              <button className="btn btn-primary" onClick={onNewPacket}>{t("nav_new")}</button>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column" }}>
              {jobs.slice(0, mode === "dashboard" ? 8 : 100).map((job) => (
                <JobRow
                  key={job.job_id}
                  job={job}
                  token={token}
                  onOpen={() => onOpenJob(job.job_id)}
                  onDelete={async () => {
                    if (!confirm(lang === "el" ? "Να διαγραφεί αυτό το job και τα αποθηκευμένα reports;" : "Delete this job and stored reports?")) return;
                    setBusyJob(job.job_id);
                    try {
                      await onDeleteJob(job.job_id);
                      setJobs((current) => current.filter((item) => item.job_id !== job.job_id));
                    } catch (err) {
                      setError(err.message || String(err));
                    } finally {
                      setBusyJob("");
                    }
                  }}
                  deleting={busyJob === job.job_id}
                  lang={lang}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, mono }) {
  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 24, color: "var(--ink-1)", fontWeight: 600, fontFamily: mono ? "var(--font-mono)" : "inherit" }}>{value}</div>
    </div>
  );
}

function JobRow({ job, token, onOpen, onDelete, deleting, lang }) {
  const status = job.status || "queued";
  const title = job.title || job.job_id;
  const pages = job.pages_processed ?? job.totals?.pages_extracted ?? 0;
  const selected = job.pages_selected ?? job.totals?.pages_selected ?? job.limits?.max_pages_per_packet ?? "?";
  const cost = Number(job.cost_summary?.estimated_cost_usd || 0);
  const terminal = ["completed", "completed_with_errors", "failed", "aborted"].includes(status);
  const reviewUrl = token
    ? `/jobs/${encodeURIComponent(job.job_id)}/review?token=${encodeURIComponent(token)}&lang=el`
    : `/jobs/${encodeURIComponent(job.job_id)}/review?lang=el`;

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "minmax(220px, 1fr) 170px 110px 120px 220px",
      gap: 14,
      alignItems: "center",
      padding: "12px 18px",
      borderTop: "1px solid var(--hairline)",
    }}>
      <div style={{ minWidth: 0 }}>
        <button className="row-btn" onClick={onOpen} style={{ color: "var(--ink-1)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {title}
        </button>
        <div className="mono muted" style={{ fontSize: 11 }}>{job.job_id}</div>
      </div>
      <StatusPill status={status}>{status}</StatusPill>
      <div className="mono">{pages} / {selected}</div>
      <div className="mono">${cost.toFixed(5)}</div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn btn-sm" onClick={onOpen}>{lang === "el" ? "Job" : "Job"}</button>
        {terminal && status !== "failed" && <a className="btn btn-primary btn-sm" href={reviewUrl}>{lang === "el" ? "Review" : "Review"}</a>}
        <button className="btn btn-sm" onClick={onDelete} disabled={deleting} style={{ color: "var(--fail)" }}>{deleting ? "..." : (lang === "el" ? "Delete" : "Delete")}</button>
      </div>
    </div>
  );
}

window.PacketsScreen = PacketsScreen;
