/* Live job progress screen */

const JOB_FILES = [
  { name: "ΒΟΥΡΔΑΜΗΣ ΒΑΣΙΛΗΣ.pdf", pages: 3 },
  { name: "ΣΥΜΒΟΛΑΙΟ 93.230-1990.pdf", pages: 11 },
  { name: "ΦΥΛΛΟ ΣΥΝΤΗΡΗΣΗΣ ΚΑΥΣΤΗΡΑ.pdf", pages: 1 },
  { name: "051090951007-0-4-KF.pdf", pages: 2 },
  { name: "example_page1.png", pages: 1 },
];
const PAGES_SELECTED = 10; // matches packet_001 totals

function statusToStep(s) {
  return ({
    queued: 0, triaging: 1,
    processing: 2, throttled: 2, retrying: 2, rate_limited: 2,
    completed: 3, completed_with_errors: 3, failed: 3,
  })[s] ?? 0;
}

function JobScreen({ status, job, jobId, token, error, onStatus, onOpenReview, onBack }) {
  const { t, lang } = useT();
  const step = statusToStep(status);
  const isActive = !["completed", "completed_with_errors", "failed"].includes(status);
  const isThrottled = status === "throttled" || status === "rate_limited" || status === "retrying";
  const files = job?.files?.length
    ? job.files.map((file) => ({ name: file.filename || file.path || "upload", pages: 1 }))
    : JOB_FILES;
  const pagesSelected = Number(job?.pages_selected || job?.limits?.max_pages_per_packet || PAGES_SELECTED);
  const pagesProcessedFromJob = Number(job?.pages_processed || 0);
  const title = job?.title || (lang === "el" ? "Νέος φάκελος ελέγχου" : "New validation packet");

  // pages_processed driven by status — gives realistic look
  const pagesProcessed = useMemo(() => {
    if (job) return pagesProcessedFromJob;
    if (status === "queued") return 0;
    if (status === "triaging") return 0;
    if (status === "processing") return 6;
    if (status === "throttled" || status === "rate_limited") return 6;
    if (status === "retrying") return 6;
    if (status === "completed") return pagesSelected;
    if (status === "completed_with_errors") return Math.max(0, pagesSelected - 1);
    if (status === "failed") return Math.min(4, pagesSelected);
    return 0;
  }, [status, job, pagesProcessedFromJob, pagesSelected]);
  const pct = pagesSelected ? Math.min(100, Math.round((pagesProcessed / pagesSelected) * 100)) : 0;

  // build per-page state
  const pageItems = useMemo(() => {
    const items = [];
    let counter = 0;
    files.forEach(f => {
      const fileItems = [];
      for (let i = 1; i <= f.pages; i++) {
        let st = "skipped";
        if (counter === pagesProcessed && isActive) st = "processing";
        else if (counter < pagesProcessed) st = "done";
        else if (counter < pagesSelected) st = "pending";
        else st = "skipped";
        // mark skipped as "not selected" — only 10 of 18 pages selected
        if (st === "skipped") st = "skipped";
        fileItems.push({ pageNum: i, st });
        counter++;
      }
      items.push({ ...f, items: fileItems });
    });
    return items;
  }, [files, pagesProcessed, pagesSelected, status, isActive]);

  const [retrySec, setRetrySec] = useState(18);
  useEffect(() => {
    if (!isThrottled) return;
    setRetrySec(18);
    const id = setInterval(() => setRetrySec(s => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, [status]);

  const statusKey = `job_status_${status}`;
  const msgKey = `job_msg_${status === "rate_limited" ? "throttled" : status === "retrying" ? "throttled" : status === "completed_with_errors" ? "completed" : status === "failed" ? "completed" : status}`;
  const reportUrl = jobId ? `/jobs/${encodeURIComponent(jobId)}/report?token=${encodeURIComponent(token || "")}` : "#";
  const packetUrl = jobId ? `/jobs/${encodeURIComponent(jobId)}/packet?token=${encodeURIComponent(token || "")}` : "#";

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      <TopBar
        eyebrow={
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <button className="btn-ghost" onClick={onBack} style={{ background: "none", border: 0, padding: 0, color: "var(--ink-4)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
              <I.arrowL size={12} /> {t("job_back")}
            </button>
            <span style={{ color: "var(--ink-5)" }}>·</span>
            <span className="mono" style={{ color: "var(--ink-4)" }}>{jobId || "job_pending"}</span>
          </span>
        }
        title={title}
        right={
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <StatusPill status={status}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                {isActive && <span className="pulse-dot" style={{
                  width: 6, height: 6, borderRadius: "50%", background: "currentColor",
                }} />}
                {t(statusKey)}
              </span>
            </StatusPill>
            {!isActive && status !== "failed" && (
              <button className="btn btn-primary" onClick={onOpenReview}>
                {t("job_open_review")} <I.arrow size={14} />
              </button>
            )}
          </div>
        }
      />

      <div style={{ display: "grid", gridTemplateColumns: "1.6fr 1fr", gap: 24, padding: "28px 36px" }}>
        {/* left: progress */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {/* big progress card */}
          <div className="card" style={{ padding: 24 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 14 }}>
              <div>
                <div className="muted" style={{ fontSize: 12, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4 }}>
                  {t(statusKey)}
                </div>
                <div className="serif" style={{ fontSize: 22, fontWeight: 500, color: "var(--ink-1)", letterSpacing: "-0.01em" }}>
                  {t(msgKey)}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div className="serif" style={{ fontSize: 32, fontWeight: 500, color: "var(--ink-1)", letterSpacing: "-0.02em", lineHeight: 1 }}>
                  {pct}<span className="muted" style={{ fontSize: 18, fontWeight: 400 }}>%</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                  {t("job_pages_done", pagesProcessed, pagesSelected)}
                </div>
              </div>
            </div>

            {/* progress bar */}
            <div style={{
              height: 6, background: "var(--paper-2)",
              borderRadius: 999, overflow: "hidden", border: "1px solid var(--hairline)",
            }}>
              <div style={{
                width: `${pct}%`, height: "100%",
                background: status === "failed" ? "var(--fail)"
                  : isThrottled ? "var(--warn)"
                  : "var(--accent)",
                transition: "width 600ms ease-out",
                position: "relative",
              }}>
                {isActive && !isThrottled && (
                  <div style={{
                    position: "absolute", inset: 0,
                    background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.4), transparent)",
                    backgroundSize: "200px 100%",
                    animation: "shimmer 1.4s linear infinite",
                  }} />
                )}
              </div>
            </div>

            {isThrottled && (
              <div style={{
                marginTop: 14,
                background: "var(--warn-bg)", border: "1px solid var(--warn-bd)",
                borderRadius: "var(--r-md)", padding: "10px 12px",
                display: "flex", alignItems: "center", gap: 10, fontSize: 13,
                color: "oklch(0.4 0.07 75)",
              }}>
                <I.clock size={14} />
                <span style={{ flex: 1 }}>
                  {status === "rate_limited"
                    ? (lang === "el" ? "Επιτεύχθηκε όριο ρυθμού. " : "Provider rate window reached. ")
                    : status === "retrying"
                    ? (lang === "el" ? "Επανάληψη μετά από σφάλμα. " : "Retrying after a transient error. ")
                    : (lang === "el" ? "Αναμονή 4 δευτ. ανάμεσα στις κλήσεις. " : "Holding 4 s between calls. ")}
                  <span className="mono">{t("job_rate_window", retrySec)}</span>
                </span>
              </div>
            )}

            {error && (
              <div style={{
                marginTop: 14,
                background: "var(--fail-bg)", border: "1px solid var(--fail-bd)",
                borderRadius: "var(--r-md)", padding: "10px 12px",
                display: "flex", alignItems: "center", gap: 10, fontSize: 13,
                color: "var(--fail)",
              }}>
                <I.alert size={14} />
                <span>{error}</span>
              </div>
            )}

            {status === "completed" && (
              <div style={{
                marginTop: 14,
                background: "var(--pass-bg)", border: "1px solid var(--pass-bd)",
                borderRadius: "var(--r-md)", padding: "10px 12px",
                display: "flex", alignItems: "center", gap: 10, fontSize: 13,
                color: "oklch(0.4 0.07 155)",
              }}>
                <I.check size={14} />
                <span>{lang === "el" ? "Έτοιμο για επισκόπηση. Ανακατεύθυνση…" : "Ready for review. Redirecting…"}</span>
              </div>
            )}

            {!isActive && status !== "failed" && (
              <div style={{ marginTop: 14, display: "flex", gap: 8 }}>
                <a className="btn btn-primary" href={reportUrl}>{lang === "el" ? "Άνοιγμα report" : "Open report"}</a>
                <a className="btn" href={packetUrl}>JSON</a>
              </div>
            )}
          </div>

          {/* steps */}
          <div className="card" style={{ padding: 20 }}>
            <div className="label" style={{ marginBottom: 12 }}>
              {lang === "el" ? "Στάδια" : "Pipeline"}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <StepRow idx={1} label={t("job_step_triage")} state={step > 1 ? "done" : step === 1 ? "active" : "pending"}
                meta={job ? `${pagesSelected} selected pages` : (lang === "el" ? "31 σελίδες σαρώθηκαν · 10 επιλέχθηκαν" : "31 pages scanned · 10 selected")} />
              <StepRow idx={2} label={t("job_step_extract")} state={step > 2 ? "done" : step === 2 ? "active" : "pending"}
                meta={lang === "el" ? "Gemini · 4s/κλήση + retry/backoff" : "Gemini · 4 s / call + retry/backoff"} />
              <StepRow idx={3} label={t("job_step_cluster")} state={step > 2 ? (status === "completed" ? "done" : "active") : "pending"}
                meta={job?.check_summary ? `${Object.values(job.check_summary).reduce((a,b)=>a+Number(b || 0),0)} checks` : (lang === "el" ? "Ομαδοποίηση και έλεγχοι" : "Clusters and checks")} />
            </div>
          </div>

          {/* files list */}
          <div className="card" style={{ padding: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 14 }}>
              <div className="label" style={{ margin: 0 }}>{t("job_files")}</div>
              <div className="muted" style={{ fontSize: 11 }}>
                {files.length} {t("files")} · {pagesSelected} {t("pages")}
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {pageItems.map((f, i) => (
                <FileBlock key={i} f={f} t={t} />
              ))}
            </div>
          </div>
        </div>

        {/* right: live evidence ticker (the inventive bit) */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18, position: "sticky", top: 28, alignSelf: "flex-start" }}>
          <EvidenceTicker status={status} pagesProcessed={pagesProcessed} />

          <div className="card" style={{ padding: 18 }}>
            <div className="label" style={{ marginBottom: 12 }}>
              {lang === "el" ? "Όρια συστήματος" : "System limits"}
            </div>
            <SmallStat label={t("job_global_lock")} value="active" mono />
            <SmallStat label={t("job_provider_min")} value="4 s" />
            <SmallStat label={lang === "el" ? "Λεπτό / Ημέρα" : "Per minute / day"} value="10 / 100" />
            <SmallStat label={t("job_estimated")} value={isActive ? "~ 38 s" : "—"} />
          </div>
        </div>
      </div>
    </div>
  );
}

function StepRow({ idx, label, state, meta }) {
  const dot = {
    pending: { bg: "var(--paper-2)", color: "var(--ink-5)", border: "var(--hairline-2)" },
    active:  { bg: "var(--accent)", color: "#fff", border: "var(--accent)" },
    done:    { bg: "var(--pass-bg)", color: "var(--pass)", border: "var(--pass-bd)" },
  }[state];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{
        width: 26, height: 26, borderRadius: "50%",
        background: dot.bg, color: dot.color,
        border: `1px solid ${dot.border}`,
        display: "grid", placeItems: "center",
        fontSize: 12, fontWeight: 500, fontFamily: "var(--font-mono)",
      }}>
        {state === "done" ? <I.check size={12} stroke={2.4} /> : idx}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13.5, color: state === "pending" ? "var(--ink-4)" : "var(--ink-1)", fontWeight: 500 }}>{label}</div>
        <div className="muted" style={{ fontSize: 11.5 }}>{meta}</div>
      </div>
      {state === "active" && (
        <span className="badge badge-accent" style={{ height: 20, fontSize: 10.5 }}>
          <span className="dot pulse-dot" /> live
        </span>
      )}
    </div>
  );
}

function FileBlock({ f, t }) {
  const done = f.items.filter(i => i.st === "done").length;
  const total = f.items.length;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <I.filePdf size={14} stroke={1.5} />
        <div className="mono" style={{
          fontSize: 12, color: "var(--ink-1)", flex: 1,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>{f.name}</div>
        <div className="muted" style={{ fontSize: 11 }}>{done}/{total}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(28px, 1fr))", gap: 4 }}>
        {f.items.map((p, i) => <PageDot key={i} st={p.st} num={p.pageNum} />)}
      </div>
    </div>
  );
}

function PageDot({ st, num }) {
  const styles = {
    done:       { bg: "var(--pass-bg)", color: "var(--pass)", border: "var(--pass-bd)" },
    processing: { bg: "var(--accent)", color: "#fff", border: "var(--accent)", anim: true },
    pending:    { bg: "var(--paper)", color: "var(--ink-5)", border: "var(--hairline-2)" },
    skipped:    { bg: "transparent", color: "var(--ink-6)", border: "var(--hairline)", dashed: true },
    failed:     { bg: "var(--fail-bg)", color: "var(--fail)", border: "var(--fail-bd)" },
  }[st];
  return (
    <div style={{
      height: 26, borderRadius: 4,
      background: styles.bg,
      border: `1px ${styles.dashed ? "dashed" : "solid"} ${styles.border}`,
      color: styles.color,
      display: "grid", placeItems: "center",
      fontSize: 10.5, fontFamily: "var(--font-mono)",
      animation: styles.anim ? "pulse-soft 1.4s ease-in-out infinite" : undefined,
    }}>
      {num}
    </div>
  );
}

function SmallStat({ label, value, mono }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "8px 0",
      borderTop: "1px solid var(--hairline)",
      fontSize: 12.5,
    }}>
      <span className="muted">{label}</span>
      <span style={{ color: "var(--ink-1)", fontFamily: mono ? "var(--font-mono)" : "inherit", fontWeight: 500 }}>{value}</span>
    </div>
  );
}

// ─── Live evidence ticker ──
// novel: as pages get processed, evidence cards stream in showing what was extracted live
const TICKER_EVENTS = [
  { time: "00:04", page: "p1", file: "ΒΟΥΡΔΑΜΗΣ", type: "afm", value: "030415831", conf: "high" },
  { time: "00:11", page: "p2", file: "ΒΟΥΡΔΑΜΗΣ", type: "registry_id", value: "10565056562", conf: "high" },
  { time: "00:17", page: "p1", file: "ΣΥΜΒΟΛΑΙΟ", type: "address", value: "ΠΛΑΤΕΙΑ 23, ΝΙΚΑΙΑ", conf: "medium", flags: ["stamp"] },
  { time: "00:23", page: "p2", file: "ΣΥΜΒΟΛΑΙΟ", type: "permit_number", value: "93.230", conf: "high", flags: ["stamp"] },
  { time: "00:29", page: "p2", file: "ΣΥΜΒΟΛΑΙΟ", type: "address", value: "Δορυλαίου 27, Νίκαια", conf: "high" },
  { time: "00:35", page: "p1", file: "051090951007", type: "kaek", value: "05 109 09 51 007 / 0 / 4", conf: "high" },
];

function EvidenceTicker({ status, pagesProcessed }) {
  const { lang } = useT();
  const visible = TICKER_EVENTS.slice(0, Math.min(TICKER_EVENTS.length, pagesProcessed));
  return (
    <div className="card" style={{ padding: 18, minHeight: 280 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
        <div className="label" style={{ margin: 0 }}>
          {lang === "el" ? "Ροή αποδείξεων" : "Evidence stream"}
        </div>
        {status === "processing" && (
          <span className="mono dim" style={{ fontSize: 11 }}>
            <span className="pulse-dot">●</span> live
          </span>
        )}
      </div>
      {visible.length === 0 ? (
        <div className="muted" style={{ fontSize: 12.5, padding: "30px 0", textAlign: "center" }}>
          {lang === "el" ? "Αναμονή πρώτης σελίδας…" : "Waiting for the first page…"}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {visible.slice().reverse().map((e, i) => <EvidenceCard key={i} e={e} fresh={i === 0 && status === "processing"} />)}
        </div>
      )}
    </div>
  );
}

function EvidenceCard({ e, fresh }) {
  const typeLabels = {
    afm: "ΑΦΜ", kaek: "ΚΑΕΚ", address: "address", permit_number: "permit", registry_id: "registry",
  };
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 10,
      padding: "8px 10px",
      borderRadius: 6,
      background: fresh ? "var(--accent-tint)" : "transparent",
      border: `1px solid ${fresh ? "var(--accent-soft)" : "var(--hairline)"}`,
      animation: fresh ? "ev-pulse 1.2s ease-out" : undefined,
    }}>
      <div className="mono dim" style={{ fontSize: 10.5, paddingTop: 2 }}>{e.time}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
          <span className="badge" style={{ height: 18, fontSize: 10, padding: "0 6px" }}>{typeLabels[e.type] || e.type}</span>
          {e.flags?.includes("stamp") && <I.stamp size={11} stroke={1.5} style={{ color: "var(--ink-5)" }} />}
          <span className="dim" style={{ fontSize: 10.5, fontFamily: "var(--font-mono)" }}>{e.file}:{e.page}</span>
        </div>
        <div className="mono" style={{ fontSize: 12, color: "var(--ink-1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {e.value}
        </div>
      </div>
      <span className={`chip`} style={{ fontSize: 10, height: 18 }}>{e.conf}</span>
    </div>
  );
}

window.JobScreen = JobScreen;
