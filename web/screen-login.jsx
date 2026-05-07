/* Login screen */

function LoginScreen({ lang, onLang, onSignIn, initialToken = "" }) {
  const { t } = useT();
  const [u, setU] = useState("stavret");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!u || !p) { setErr(t("invalid_creds")); return; }
    setLoading(true);
    try {
      await onSignIn(u, p);
    } catch (error) {
      setErr(error.message || t("invalid_creds"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{
      minHeight: "100vh",
      background: "var(--bg)",
      display: "grid",
      gridTemplateColumns: "1fr 1.1fr",
    }}>
      {/* left: form */}
      <div style={{ padding: "40px 56px", display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <BrandLockup />
          <LangSwitch lang={lang} onChange={onLang} />
        </div>

        <div style={{ flex: 1, display: "grid", placeItems: "center", padding: "20px 0" }}>
          <div style={{ width: "100%", maxWidth: 360 }}>
            <div className="muted" style={{ fontSize: 12, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 10 }}>
              {t("tagline")}
            </div>
            <h1 className="serif" style={{ margin: "0 0 6px", fontSize: 30, fontWeight: 600, color: "var(--ink-1)", letterSpacing: "-0.02em" }}>
              {t("login_title")}
            </h1>
            <p className="muted" style={{ margin: "0 0 28px", fontSize: 14 }}>
              {t("login_subtitle")}
            </p>

            <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <label className="label" htmlFor="u">{t("username")}</label>
                <input id="u" className="input" autoFocus value={u}
                  onChange={e => { setU(e.target.value); setErr(""); }} />
              </div>
              <div>
                <label className="label" htmlFor="p">{t("password")}</label>
                <div style={{ position: "relative" }}>
                  <input id="p" className="input" type={showPassword ? "text" : "password"} value={p}
                    onChange={e => { setP(e.target.value); setErr(""); }}
                    style={{ paddingRight: 44 }} />
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setShowPassword((value) => !value)}
                    title={showPassword ? "Hide password" : "Show password"}
                    style={{
                      position: "absolute",
                      right: 8,
                      top: "50%",
                      transform: "translateY(-50%)",
                      border: 0,
                      background: "transparent",
                      color: "var(--ink-4)",
                      display: "grid",
                      placeItems: "center",
                      cursor: "pointer",
                    }}
                  >
                    {showPassword ? <I.eyeOff size={16} /> : <I.eye size={16} />}
                  </button>
                </div>
              </div>

              {err && (
                <div style={{
                  fontSize: 12.5, color: "var(--fail)",
                  background: "var(--fail-bg)",
                  border: "1px solid var(--fail-bd)",
                  padding: "8px 10px",
                  borderRadius: "var(--r-md)",
                  display: "flex", alignItems: "center", gap: 8,
                }}>
                  <I.alert size={14} />
                  {err}
                </div>
              )}

              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>
                <input type="checkbox" defaultChecked style={{ accentColor: "var(--accent)" }} />
                {t("remember")}
              </label>

              <button type="submit" className="btn btn-primary" style={{ height: 42, marginTop: 4, justifyContent: "center" }} disabled={loading}>
                {loading ? <I.spin size={14} className="pulse-dot" /> : null}
                {t("sign_in")}
              </button>
            </form>

            <div style={{ marginTop: 28, paddingTop: 18, borderTop: "1px solid var(--hairline)" }}>
              <div style={{ display: "flex", alignItems: "flex-start", gap: 10, fontSize: 12.5, color: "var(--ink-4)" }}>
                <I.lock size={14} />
                <div>
                  <div style={{ color: "var(--ink-3)", marginBottom: 2 }}>{t("contact_admin")}</div>
                  <div>{t("forgot_note")}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="muted" style={{ fontSize: 11.5, display: "flex", justifyContent: "space-between" }}>
          <span>© 2026 arch-ocr</span>
          <span className="mono">v0.4.0 · ΕΛ/EN</span>
        </div>
      </div>

      {/* right: document panel */}
      <div style={{
        background: "var(--accent)",
        position: "relative",
        overflow: "hidden",
      }}>
        <div style={{
          position: "absolute", inset: 0,
          background: `
            radial-gradient(ellipse at 30% 20%, rgba(255,255,255,0.08), transparent 60%),
            linear-gradient(135deg, var(--accent) 0%, #0d1428 100%)
          `,
        }} />
        {/* faux document */}
        <div style={{
          position: "absolute", left: "12%", top: "16%", right: "16%", bottom: "16%",
          background: "#fdfcf7",
          borderRadius: 4,
          boxShadow: "0 30px 80px -20px rgba(0,0,0,0.5), 0 10px 30px -10px rgba(0,0,0,0.3)",
          padding: "32px 36px",
          fontFamily: "var(--font-mono)",
          fontSize: 10.5,
          color: "var(--ink-3)",
          overflow: "hidden",
        }}>
          <div style={{ borderBottom: "1px solid var(--hairline)", paddingBottom: 10, marginBottom: 14, display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div>
              <div className="serif" style={{ fontSize: 16, fontWeight: 600, color: "var(--ink-1)", letterSpacing: "-0.01em" }}>
                ΕΛΛΗΝΙΚΗ ΔΗΜΟΚΡΑΤΙΑ
              </div>
              <div style={{ fontSize: 10, marginTop: 2 }}>ΥΠΟΥΡΓΕΙΟ ΠΕΡΙΒΑΛΛΟΝΤΟΣ · ΑΔΕΙΑ ΟΙΚΟΔΟΜΗΣ</div>
            </div>
            <div className="badge badge-pass" style={{ height: 20, fontSize: 10 }}>
              <span className="dot" /> verified
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "100px 1fr", rowGap: 8, columnGap: 16, fontSize: 11 }}>
            <div className="dim">permit_no</div><div style={{ color: "var(--ink-1)" }}>93.230 / 1990</div>
            <div className="dim">kaek</div><div style={{ color: "var(--ink-1)" }}>05 109 09 51 007 / 0 / 4</div>
            <div className="dim">afm</div><div style={{ color: "var(--ink-1)" }}>030415831</div>
            <div className="dim">διεύθυνση</div><div style={{ color: "var(--ink-1)" }}>Δορυλαίου 27, Νίκαια</div>
            <div className="dim">ιδιοκτήτης</div><div style={{ color: "var(--ink-1)" }}>Κ. χήρα Γ. Βουρδαμή</div>
            <div className="dim">μηχανικός</div><div style={{ color: "var(--ink-1)" }}>Π. Σταθάτος</div>
            <div className="dim">ημερομηνία</div><div style={{ color: "var(--ink-1)" }}>2 Οκτωβρίου 1990</div>
          </div>

          <div style={{ marginTop: 22, paddingTop: 14, borderTop: "1px solid var(--hairline)" }}>
            <div className="dim" style={{ marginBottom: 8 }}>checks</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <CheckRow status="pass"  label="KAEK consistency" />
              <CheckRow status="pass"  label="AFM evidence" />
              <CheckRow status="warning" label="Address consistency · 5 clusters" />
              <CheckRow status="warning" label="Handwritten content · 39 fields" />
              <CheckRow status="pass"  label="Permit number presence" />
              <CheckRow status="unknown" label="ATAK evidence" />
            </div>
          </div>

          {/* fake stamp */}
          <div style={{
            position: "absolute", right: 30, bottom: 28,
            width: 90, height: 90,
            border: "1.5px solid oklch(0.55 0.13 25 / 0.55)",
            borderRadius: "50%",
            transform: "rotate(-8deg)",
            display: "grid", placeItems: "center",
            color: "oklch(0.55 0.13 25 / 0.7)",
            fontSize: 8, textAlign: "center",
            lineHeight: 1.2,
          }}>
            <div>
              ΔΗΜΟΣ<br/>ΝΙΚΑΙΑΣ<br/>1990
            </div>
          </div>
        </div>

        <div style={{
          position: "absolute", left: 56, bottom: 36,
          color: "rgba(255,255,255,0.85)",
          maxWidth: 360,
        }}>
          <div className="serif" style={{ fontSize: 22, fontWeight: 500, lineHeight: 1.3, letterSpacing: "-0.01em" }}>
            “Built for the kind of careful work a notary trusts on a Tuesday morning.”
          </div>
          <div style={{ fontSize: 12, marginTop: 12, opacity: 0.65, letterSpacing: "0.04em", textTransform: "uppercase" }}>
            Arch-ocr · evidence ledger
          </div>
        </div>
      </div>
    </div>
  );
}

function CheckRow({ status, label }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 10.5, color: "var(--ink-2)" }}>
      <StatusPill status={status} size="sm">{status}</StatusPill>
      <span style={{ flex: 1 }}>{label}</span>
    </div>
  );
}

window.LoginScreen = LoginScreen;
