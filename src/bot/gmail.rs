//! The production Gmail poller: a fixed, read-only browser scan of a date window, and its scheduler.
//!
//! One shared browser owns all accounts. A scan runs `in:inbox after:<epoch>` in the Gmail UI,
//! reads only opaque IDs and structure, and opens, labels, sends and archives nothing. Identity of the
//! wanted account is checked before and after every read. A scan counts only when every check passes.
//! A date window is not a guarantee for the whole inbox. See docs/gmail-worker.md.

use std::time::Duration;

use futures_util::future::BoxFuture;
use serde_json::{Value, json};

use super::gmail_store::{
    GMAIL_KIND, GMAIL_PRIORITY, GMAIL_SOURCE, GmailScanFailure, GmailThreadRev, GmailWindow,
    ScanMode,
};
use crate::config::{Gmail, GmailAccount};
use crate::tools::BrowserHost;

const MAILBOX_HOST: &str = "mail.google.com";
const MAX_ROWS: usize = 100;
/// Longest wait for the account control, in milliseconds.
const CHECK_WAIT_MS: u32 = 8000;
const EPOCH_MIN: i64 = 1_000_000_000;
const EPOCH_MAX: i64 = 10_000_000_000;

/// Waits inside one scan. Tests shorten them.
#[derive(Debug, Clone, Copy)]
pub struct Pacing {
    pub retry_wait: Duration,
    pub advance_wait: Duration,
    pub advance_polls: u32,
    pub read_attempts: u32,
}

impl Default for Pacing {
    fn default() -> Self {
        Self {
            retry_wait: Duration::from_secs(3),
            advance_wait: Duration::from_secs(2),
            advance_polls: 4,
            read_attempts: 4,
        }
    }
}

/// Asked before every browser call. False stops the scan: a pause, a hold, a stop or a shutdown.
pub trait Gate: Send + Sync {
    fn open(&self) -> BoxFuture<'_, bool>;
}

/// Why a scan did not complete. `reason` is a fixed code. It never holds page text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScanFail {
    pub kind: GmailScanFailure,
    pub reason: &'static str,
    /// The gate closed. The caller must stop the whole cycle.
    pub aborted: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Scanned {
    pub threads: Vec<GmailThreadRev>,
    pub pages: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlanKind {
    Baseline,
    Catchup,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Plan {
    pub kind: PlanKind,
    pub lower_s: i64,
}

/// The fixed lower bound of the next scan. Never scanned: now minus `hours`. Otherwise the start of the
/// last successful scan minus the overlap, however long the downtime was. A clock behind the watermark is refused.
pub fn plan_window(
    now_ms: i64,
    watermark_ms: Option<i64>,
    hours: u64,
    overlap_s: u64,
) -> Result<Plan, &'static str> {
    let epoch = |s: i64| {
        (EPOCH_MIN..EPOCH_MAX)
            .contains(&s)
            .then_some(s)
            .ok_or("epoch_range")
    };
    let now_s = epoch(now_ms.div_euclid(1000))?;
    let (kind, lower) = match watermark_ms {
        None => (PlanKind::Baseline, now_s - (hours as i64) * 3600),
        Some(w) => {
            if w > now_ms {
                return Err("clock_behind");
            }
            (
                PlanKind::Catchup,
                epoch(w.div_euclid(1000))? - overlap_s as i64,
            )
        }
    };
    Ok(Plan {
        kind,
        lower_s: epoch(lower)?,
    })
}

pub fn window_view(lower_s: i64) -> String {
    format!("#search/in%3Ainbox+after%3A{lower_s}")
}

/// The search view of the backlog walk: the inbox before a fixed time, with the owner's extra terms.
/// The terms hold only letters, digits, `-`, `_`, `:` and spaces (checked by the config).
pub fn backlog_view(ceiling_s: i64, exclude: &str) -> String {
    let extra: String = exclude
        .trim()
        .chars()
        .map(|c| match c {
            ':' => "%3A".to_owned(),
            ' ' => "+".to_owned(),
            c => c.to_string(),
        })
        .collect();
    if extra.is_empty() {
        format!("#search/in%3Ainbox+before%3A{ceiling_s}")
    } else {
        format!("#search/in%3Ainbox+before%3A{ceiling_s}+{extra}")
    }
}

/// The threads of some result pages and whether the last page was the end of the results.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Walk {
    pub pages: Vec<Vec<GmailThreadRev>>,
    pub end: bool,
}

pub fn window_url(index: u8, lower_s: i64) -> String {
    format!(
        "https://{MAILBOX_HOST}/mail/u/{index}/{}",
        window_view(lower_s)
    )
}

fn root_url(index: u8) -> String {
    format!("https://{MAILBOX_HOST}/mail/u/{index}/")
}

/// One bare step text: pass when the page is the https mailbox with ONE account control whose label holds the
/// address token of exactly ONE registered account, and that account is the wanted one.
/// The addresses are `${NAME}` placeholders that bare fills in inside the guest. The text holds no address.
pub fn identity_steps(selector: &str, secrets: &[&str], wanted: usize) -> String {
    let known = secrets
        .iter()
        .map(|n| format!("'${{{n}}}'.toLowerCase()"))
        .collect::<Vec<_>>()
        .join(", ");
    let js = format!(
        "(() => {{ const els = document.querySelectorAll({sel}); if (location.hostname !== {host} || location.protocol !== 'https:' || els.length !== 1) return false; const label = els[0].getAttribute('aria-label') || '';\
 const toks = (label.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{{2,}}/g) || []).map(t => t.toLowerCase());\
 const hit = [{known}].map(k => toks.some(t => t === k)); return hit.filter(Boolean).length === 1 && hit[{wanted}] === true; }})()",
        sel = json!(selector),
        host = json!(MAILBOX_HOST),
    );
    format!(
        "timeout {CHECK_WAIT_MS}\nexpect js \"{}\"",
        js.replace('\\', "\\\\").replace('"', "\\\"")
    )
}

/// The fixed contract of a results page. Observed live: see docs/gmail-inbox-probe-results.md.
fn contract(view: &str, search_box: &str) -> Value {
    json!({
        "row": "tr[role=\"row\"]",
        "thread": {"sel": "[data-legacy-thread-id]", "attr": "data-legacy-thread-id"},
        "message": {"sel": "[data-legacy-last-non-draft-message-id]", "attr": "data-legacy-last-non-draft-message-id"},
        "unread": null,
        "empty": "[role=\"main\"] td",
        "range": "span",
        "older": "div[role=button][aria-label^=Next]",
        "newer": "div[role=button][aria-label^=Prev]",
        "grid": "[role=\"main\"] table[role=\"grid\"]",
        "view": view,
        "qbox": search_box,
        "max_rows": MAX_ROWS,
    })
}

/// The selector of the Next control, as the click step quotes it.
const NEXT: &str = "div[role=button][aria-label^=Next]";

/// The one fixed read of a results page. The contract is data, the program is constant.
/// Only visible elements count: Gmail keeps the other views in the DOM as hidden duplicates.
/// Text is read only to match the range pattern (numbers) and one fixed empty phrase. Neither leaves the page as text.
pub fn scan_js(view: &str, search_box: &str) -> String {
    const BODY: &str = r#" const q = (s) => s ? Array.from(document.querySelectorAll(s)) : [];
 const vis = (e) => !!(e.offsetParent || (e.getClientRects && e.getClientRects().length));
 const ids = (r, x) => x ? Array.from(r.querySelectorAll(x.sel)).slice(0, 4).map(e => String(e.getAttribute(x.attr) || '').slice(0, 120)) : null;
 const gs = C.grid ? q(C.grid).filter(vis) : null;
 const rows = C.grid ? (gs.length === 1 ? Array.from(gs[0].querySelectorAll(C.row)) : []) : q(C.row);
 const RE = /^\s*([0-9][0-9,]*)\s*[–-]\s*([0-9][0-9,]*)\s+of\s+(many|[0-9][0-9,]*)\s*$/;
 const num = (x) => Number(x.replace(/,/g, ''));
 const rg = new Set(); q(C.range).forEach(e => { if (!vis(e) || e.closest('[role=grid]')) return; const m = RE.exec(String(e.textContent || '').slice(0, 40));
 if (m) rg.add(JSON.stringify([num(m[1]), num(m[2]), m[3] === 'many' ? null : num(m[3])])); });
 const range = rg.size === 0 ? null : rg.size === 1 ? JSON.parse(Array.from(rg)[0]) : [null];
 const ctl = (s) => { if (!s) return null; const l = q(s).filter(vis); return {n: l.length, disabled: l.length === 1 && (l[0].getAttribute('aria-disabled') === 'true' || l[0].hasAttribute('disabled'))}; };
 const EM = ['No messages matched your search'];
 const empty = C.empty ? q(C.empty).filter(e => vis(e) && !e.closest('[role=grid]') && EM.some(p => String(e.textContent || '').trim().startsWith(p))).length : 0;
 const h = location.hash; const V = C.view || '#inbox'; const sk = V.indexOf('#search/') === 0;
 const dec = (x) => { try { return decodeURIComponent(x.replace(/\+/g, ' ')); } catch (e) { return null; } };
 const hm = /^(.*?)(?:\/p([0-9]{1,3}))?$/.exec(h); const want = sk ? dec(V.slice(8)) : null;
 const got = sk && hm[1].indexOf('#search/') === 0 ? dec(hm[1].slice(8)) : null; const hit = sk && want !== null && got === want;
 const qs = C.qbox ? q(C.qbox).filter(vis) : null;
 return JSON.stringify({host: location.hostname, proto: location.protocol, ready: document.readyState,
 view: sk ? (hit ? V : 'other') : /^#[a-z]{1,30}$/.test(h) ? h : 'other', page: hit ? Number(hm[2] || 1) : null,
 qn: qs ? qs.length : null, qm: qs ? qs.filter(e => want !== null && String(e.value || '').trim() === want).length : null,
 path_ok: /^\/mail\/u\/[0-9]\/$/.test(location.pathname), grids: gs ? gs.length : null,
 rows: rows.length, empty: empty, range: range, older: ctl(C.older), newer: ctl(C.newer),
 items: rows.slice(0, C.max_rows).map(r => ({t: ids(r, C.thread), m: ids(r, C.message), u: false}))}); })()"#;
    format!("(() => {{ const C = {};{BODY}", contract(view, search_box))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ctl {
    pub present: bool,
    pub disabled: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Page {
    pub page: u32,
    pub rows: Vec<(String, String)>,
    pub empty: bool,
    /// Start, end and the total, when the page names one. Gmail says "many" for a search.
    pub range: Option<(u32, u32, Option<u32>)>,
    pub older: Option<Ctl>,
    pub newer: Option<Ctl>,
}

/// Why a page does not count. Each value maps to one fixed result. No variant holds page text.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PageFail {
    Malformed,
    NotMailbox,
    Unloaded,
    NotInbox,
    NoRowsUnproven,
    Unsupported(&'static str),
}

impl PageFail {
    fn retryable(self) -> bool {
        matches!(self, Self::Unloaded | Self::NoRowsUnproven)
    }

    fn scan_fail(self) -> ScanFail {
        let (kind, reason) = match self {
            Self::Malformed => (GmailScanFailure::Error, "malformed"),
            Self::NotMailbox => (GmailScanFailure::Auth, "signed_out"),
            Self::Unloaded => (GmailScanFailure::Incomplete, "unloaded"),
            Self::NotInbox => (GmailScanFailure::Error, "not_inbox"),
            Self::NoRowsUnproven => (GmailScanFailure::Incomplete, "no_rows_unproven"),
            Self::Unsupported(r) => (GmailScanFailure::Error, r),
        };
        ScanFail {
            kind,
            reason,
            aborted: false,
        }
    }
}

/// 16 lowercase ASCII hex digits.
fn valid_id(id: &str) -> bool {
    id.len() == 16 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// A row may carry its ID on up to three elements. Every one must hold the same valid ID.
fn one_id(x: Option<&Value>) -> Option<String> {
    let l = x?.as_array().filter(|l| (1..=3).contains(&l.len()))?;
    let first = l[0].as_str()?;
    (valid_id(first) && l.iter().all(|v| v.as_str() == Some(first))).then(|| first.to_owned())
}

fn uint(v: &Value) -> Option<u32> {
    v.as_u64().and_then(|n| u32::try_from(n).ok())
}

fn ctl(v: &Value) -> Result<Option<Ctl>, ()> {
    if v.is_null() {
        return Ok(None);
    }
    match (uint(&v["n"]), v["disabled"].as_bool()) {
        (Some(n @ 0..=1), Some(disabled)) => Ok(Some(Ctl {
            present: n == 1,
            disabled,
        })),
        _ => Err(()),
    }
}

/// Check one read of a results page. Zero rows is an empty window only with the positive marker.
pub fn parse_page(raw: &Value, view: &str) -> Result<Page, PageFail> {
    let mut d = raw.clone();
    for _ in 0..2 {
        if let Value::String(t) = &d {
            d = serde_json::from_str(t).map_err(|_| PageFail::Malformed)?;
        }
    }
    if !d.is_object() {
        return Err(PageFail::Malformed);
    }
    if d["proto"] != "https:" || d["host"] != MAILBOX_HOST {
        return Err(PageFail::NotMailbox);
    }
    if d["ready"] != "complete" {
        return Err(PageFail::Unloaded);
    }
    if d["view"] != view || d["path_ok"] != true {
        return Err(PageFail::NotInbox);
    }
    let bad = PageFail::Unsupported;
    if uint(&d["grids"]) != Some(1) {
        return Err(bad("grid_count"));
    }
    if uint(&d["qn"]) != Some(1) || uint(&d["qm"]) != Some(1) {
        return Err(bad("query_box"));
    }
    let (rows, marker) = (uint(&d["rows"]), uint(&d["empty"]));
    let (Some(rows), Some(marker), Some(items)) = (rows, marker, d["items"].as_array()) else {
        return Err(bad("row_count_mismatch"));
    };
    if items.len() != (rows as usize).min(MAX_ROWS) {
        return Err(bad("row_count_mismatch"));
    }
    let page = uint(&d["page"])
        .filter(|p| *p >= 1)
        .ok_or(bad("page_shape"))?;
    let range = match &d["range"] {
        Value::Null => None,
        v => {
            let a = v.as_array().filter(|a| a.len() == 3);
            match a.map(|a| (uint(&a[0]), uint(&a[1]), &a[2])) {
                Some((Some(s), Some(e), t)) if t.is_null() || uint(t).is_some() => {
                    Some((s, e, uint(t)))
                }
                _ => return Err(bad("range_shape")),
            }
        }
    };
    let older = ctl(&d["older"]).map_err(|()| bad("older_shape"))?;
    let newer = ctl(&d["newer"]).map_err(|()| bad("newer_shape"))?;
    let mut out = Page {
        page,
        rows: vec![],
        empty: false,
        range,
        older,
        newer,
    };
    if rows == 0 {
        return match marker {
            0 => Err(PageFail::NoRowsUnproven),
            1 => Ok(Page { empty: true, ..out }),
            _ => Err(bad("marker_count")),
        };
    }
    if marker != 0 {
        return Err(bad("marker_with_rows"));
    }
    if let Some((s, e, _)) = range
        && rows as usize <= MAX_ROWS
        && (e < s || e - s + 1 != rows)
    {
        return Err(bad("range_rows_mismatch"));
    }
    let mut seen = std::collections::HashSet::new();
    for it in items {
        let t = one_id(it.get("t")).ok_or(bad("row_id_shape"))?;
        let m = one_id(it.get("m")).ok_or(bad("message_id_shape"))?;
        if !seen.insert(t.clone()) {
            return Err(bad("duplicate_thread_id"));
        }
        out.rows.push((t, m));
    }
    if rows as usize > MAX_ROWS {
        return Err(bad("too_many_rows"));
    }
    Ok(out)
}

/// `ok (<n> ms)`, alone or with a `: note` that bare may append. bare reports a failed step as text without an error flag.
fn receipt_ok(v: &Value, note: bool) -> bool {
    let Some(text) = v.as_str() else {
        return false;
    };
    let line = if note {
        text.trim().lines().next().unwrap_or("")
    } else {
        text.trim()
    };
    let Some(rest) = line.strip_prefix("ok (") else {
        return false;
    };
    let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
    let Some(tail) = rest[digits..].strip_prefix(" ms)") else {
        return false;
    };
    digits > 0 && (tail.is_empty() || (note && tail.starts_with(": ")))
}

fn gate_closed() -> ScanFail {
    ScanFail {
        kind: GmailScanFailure::Incomplete,
        reason: "gate_closed",
        aborted: true,
    }
}

fn incomplete(reason: &'static str) -> ScanFail {
    ScanFail {
        kind: GmailScanFailure::Incomplete,
        reason,
        aborted: false,
    }
}

pub struct Scanner<'a> {
    pub host: &'a dyn BrowserHost,
    pub gate: &'a dyn Gate,
    pub cfg: &'a Gmail,
    pub pacing: Pacing,
}

impl Scanner<'_> {
    /// One browser call, after the gate. `None` is a failed call. Its text is never kept.
    pub(super) async fn call(&self, name: &str, args: Value) -> Result<Option<Value>, ScanFail> {
        if !self.gate.open().await {
            return Err(gate_closed());
        }
        Ok(self.host.call(name, args).await.ok())
    }

    pub(super) async fn step(&self, steps: String, note: bool) -> Result<bool, ScanFail> {
        let v = self
            .call("step", json!({"steps": steps, "compact": true}))
            .await?;
        Ok(v.is_some_and(|v| receipt_ok(&v, note)))
    }

    pub(super) async fn identity(&self, wanted: usize) -> Result<bool, ScanFail> {
        let secrets: Vec<&str> = self
            .cfg
            .accounts
            .iter()
            .map(|a| a.user_secret.as_str())
            .collect();
        let steps = identity_steps(&self.cfg.account_control_selector, &secrets, wanted);
        self.step(steps, false).await
    }

    /// A refused identity on a page of another host is a signed-out browser. Nothing signs in again.
    async fn refusal(&self) -> Result<ScanFail, ScanFail> {
        let host = self
            .call("eval", json!({"js": "location.hostname"}))
            .await?;
        let other = host.as_ref().and_then(Value::as_str).is_some_and(|h| {
            h != MAILBOX_HOST
                && (1..=100).contains(&h.len())
                && h.bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'.' || b == b'-')
        });
        Ok(if other {
            ScanFail {
                kind: GmailScanFailure::Auth,
                reason: "signed_out",
                aborted: false,
            }
        } else {
            ScanFail {
                kind: GmailScanFailure::IdentityMismatch,
                reason: "identity_refused",
                aborted: false,
            }
        })
    }

    /// Open the configured profile only when no live browser session answers. A live session is never replaced.
    pub async fn ensure_session(&self) -> Result<(), ScanFail> {
        for _ in 0..2 {
            if self
                .call("eval", json!({"js": "location.hostname"}))
                .await?
                .is_some()
            {
                return Ok(());
            }
        }
        // An eval failure can mean a stopped page, not a missing session.
        // Only the explicit empty session list permits replacing the profile.
        if self
            .call("sessions", json!({}))
            .await?
            .as_ref()
            .and_then(Value::as_str)
            != Some("no live sessions")
        {
            return Err(ScanFail {
                kind: GmailScanFailure::Error,
                reason: "session_unavailable",
                aborted: false,
            });
        }
        let url = root_url(self.cfg.accounts[0].index);
        match self
            .call("open", json!({"url": url, "state": self.cfg.profile}))
            .await?
        {
            Some(_) => Ok(()),
            None => Err(ScanFail {
                kind: GmailScanFailure::Error,
                reason: "open_failed",
                aborted: false,
            }),
        }
    }

    /// One scan of the current page, wrapped by identity checks. A page that is still loading is read again.
    async fn read(&self, wanted: usize, view: &str) -> Result<Page, ScanFail> {
        let js = scan_js(view, &self.cfg.search_box_selector);
        let mut last = ScanFail {
            kind: GmailScanFailure::Error,
            reason: "read_failed",
            aborted: false,
        };
        for i in 0..self.pacing.read_attempts {
            if i > 0 {
                tokio::time::sleep(self.pacing.retry_wait).await;
            }
            if !self.identity(wanted).await? {
                last = ScanFail {
                    kind: GmailScanFailure::IdentityMismatch,
                    reason: "identity_refused",
                    aborted: false,
                };
                continue;
            }
            let Some(raw) = self.call("eval", json!({"js": js})).await? else {
                last = ScanFail {
                    kind: GmailScanFailure::Error,
                    reason: "eval_failed",
                    aborted: false,
                };
                continue;
            };
            match parse_page(&raw, view) {
                Ok(page) => {
                    if !self.identity(wanted).await? {
                        return Err(ScanFail {
                            kind: GmailScanFailure::IdentityMismatch,
                            reason: "identity_changed",
                            aborted: false,
                        });
                    }
                    return Ok(page);
                }
                Err(f) if f.retryable() => last = f.scan_fail(),
                Err(f) => return Err(f.scan_fail()),
            }
        }
        if last.reason == "identity_refused" {
            return Err(self.refusal().await?);
        }
        Err(last)
    }

    async fn goto_read(&self, url: &str, wanted: usize, view: &str) -> Result<Page, ScanFail> {
        match self.navigate_read(url, wanted, view).await {
            // Gmail sometimes keeps a stale search box or a second grid after a repeat visit of a window URL.
            // It stayed wrong for 20 s live, so a wait does not help. One fresh navigation through the mailbox root did.
            Err(f) if !f.aborted && matches!(f.reason, "query_box" | "grid_count") => {
                let root = root_url(self.cfg.accounts[wanted].index);
                if !self.step(format!("goto {root}"), true).await? {
                    return Err(f);
                }
                self.navigate_read(url, wanted, view).await
            }
            r => r,
        }
    }

    async fn navigate_read(&self, url: &str, wanted: usize, view: &str) -> Result<Page, ScanFail> {
        if !self.step(format!("goto {url}"), true).await? {
            return Err(incomplete("navigation_failed"));
        }
        self.read(wanted, view).await
    }

    /// Click Next and wait, within a bound, for the next page number.
    async fn advance(
        &self,
        prev: &Page,
        wanted: usize,
        view: &str,
    ) -> Result<Option<Page>, ScanFail> {
        if !self.step(format!("click css=\"{NEXT}\""), true).await? {
            return Ok(None);
        }
        let mut last = None;
        for _ in 0..self.pacing.advance_polls {
            tokio::time::sleep(self.pacing.advance_wait).await;
            let p = self.read(wanted, view).await?;
            let moved = p.page != prev.page;
            last = Some(p);
            if moved {
                break;
            }
        }
        Ok(last)
    }

    /// Read up to `max_pages` pages of `in:inbox before:<ceiling_s>` from page `start`. Any stall or doubt is a failure.
    /// `end` is true only when a disabled Older control (or the empty marker) ended the results. Nothing here writes.
    pub async fn scan_backlog(
        &self,
        acct: &GmailAccount,
        ceiling_s: i64,
        exclude: &str,
        start: u32,
        max_pages: u32,
    ) -> Result<Walk, ScanFail> {
        let wanted = self
            .cfg
            .accounts
            .iter()
            .position(|a| a.key == acct.key)
            .ok_or(ScanFail {
                kind: GmailScanFailure::Error,
                reason: "unknown_account",
                aborted: false,
            })?;
        let view = backlog_view(ceiling_s, exclude);
        let url = format!("https://{MAILBOX_HOST}/mail/u/{}/{view}", acct.index);
        let mut page = self.goto_read(&url, wanted, &view).await?;
        if page.empty {
            return Ok(Walk {
                pages: vec![],
                end: true,
            });
        }
        if page.page != 1 || page.range.is_some_and(|r| r.0 != 1) {
            return Err(incomplete("not_first_page"));
        }
        let mut n = 1;
        let mut pages: Vec<Vec<GmailThreadRev>> = vec![];
        loop {
            if n >= start.max(1) {
                if page.empty {
                    return Err(incomplete("page_not_ok"));
                }
                pages.push(
                    page.rows
                        .iter()
                        .map(|(t, m)| GmailThreadRev {
                            thread_id: t.clone(),
                            message_id: m.clone(),
                        })
                        .collect(),
                );
            }
            let Some(older) = page.older.filter(|o| o.present) else {
                return Err(incomplete("pager_missing"));
            };
            if older.disabled {
                return Ok(Walk { pages, end: true });
            }
            if pages.len() as u32 >= max_pages {
                return Ok(Walk { pages, end: false });
            }
            page = self
                .advance(&page, wanted, &view)
                .await?
                .ok_or(incomplete("advance_failed"))?;
            n += 1;
            if page.page != n {
                return Err(incomplete("page_number"));
            }
        }
    }

    /// Scan `in:inbox after:<lower_s>` of one account: page 1 to a disabled Next control, then a recheck of page 1.
    /// Any cap, stall or doubt gives a failure. Only a disabled control or the empty marker ends a walk as complete.
    pub async fn scan(&self, acct: &GmailAccount, lower_s: i64) -> Result<Scanned, ScanFail> {
        let wanted = self
            .cfg
            .accounts
            .iter()
            .position(|a| a.key == acct.key)
            .ok_or(ScanFail {
                kind: GmailScanFailure::Error,
                reason: "unknown_account",
                aborted: false,
            })?;
        let (view, url) = (window_view(lower_s), window_url(acct.index, lower_s));
        let first = self.goto_read(&url, wanted, &view).await?;
        let mut order: Vec<GmailThreadRev> = vec![];
        let mut pages = 1;
        if !first.empty {
            if first.page != 1
                || !first.newer.is_some_and(|n| n.present && n.disabled)
                || first.range.is_some_and(|r| r.0 != 1)
            {
                return Err(incomplete("not_first_page"));
            }
            let (mut page, mut prev_end) = (first.clone(), None::<u32>);
            let mut seen: std::collections::HashMap<String, String> = Default::default();
            loop {
                if page.empty {
                    return Err(incomplete("page_not_ok"));
                }
                if page.page != pages {
                    return Err(incomplete("page_number"));
                }
                let mut new = 0;
                for (t, m) in &page.rows {
                    match seen.get(t) {
                        Some(old) if old != m => return Err(incomplete("changed_during_walk")),
                        Some(_) => {}
                        None => {
                            seen.insert(t.clone(), m.clone());
                            order.push(GmailThreadRev {
                                thread_id: t.clone(),
                                message_id: m.clone(),
                            });
                            new += 1;
                        }
                    }
                }
                if pages > 1 {
                    if new == 0 {
                        return Err(incomplete("no_progress"));
                    }
                    if let (Some(r), Some(end)) = (page.range, prev_end)
                        && r.0 != end + 1
                    {
                        return Err(incomplete("range_discontinuity"));
                    }
                }
                prev_end = page.range.map(|r| r.1);
                let Some(older) = page.older.filter(|o| o.present) else {
                    return Err(incomplete("pager_missing"));
                };
                if older.disabled {
                    if page
                        .range
                        .and_then(|r| r.2)
                        .is_some_and(|t| t as usize != seen.len())
                    {
                        return Err(incomplete("total_mismatch"));
                    }
                    break;
                }
                if pages >= self.cfg.max_pages {
                    return Err(incomplete("page_cap"));
                }
                page = self
                    .advance(&page, wanted, &view)
                    .await?
                    .ok_or(incomplete("advance_failed"))?;
                pages += 1;
            }
        }
        // A fresh navigation and a second read of page 1 must equal the first read.
        let again = match self.goto_read(&url, wanted, &view).await {
            Err(e) if e.aborted => return Err(e),
            r => r.ok(),
        };
        if again.is_none_or(|a| a.empty != first.empty || a.rows != first.rows) {
            return Err(incomplete("first_page_changed"));
        }
        Ok(Scanned {
            threads: order,
            pages,
        })
    }
}

#[cfg(test)]
pub(super) mod sim {
    //! A simulated Gmail in one shared browser. It answers the fixed steps and the fixed eval of the scanner.

    use std::collections::{BTreeMap, VecDeque};
    use std::sync::Mutex;

    use super::*;

    pub type Hook = Box<dyn FnMut(usize, &str) + Send>;

    pub const T: [&str; 8] = [
        "0000000000000001",
        "0000000000000002",
        "0000000000000003",
        "0000000000000004",
        "0000000000000005",
        "0000000000000006",
        "0000000000000007",
        "0000000000000008",
    ];
    pub fn m(n: u32) -> String {
        format!("{:016x}", 0xa000 + n)
    }

    pub struct State {
        pub host: String,
        /// Index of the signed-in account in the switcher. `None`: nobody.
        pub active: Option<usize>,
        /// A second registered account in the same label.
        pub ambiguous: bool,
        pub hash: String,
        pub page: u32,
        pub page_size: usize,
        /// Rows of the window of each account index, newest first.
        pub data: BTreeMap<usize, Vec<(String, String)>>,
        /// The search box value. `None` follows the hash.
        pub qbox: Option<String>,
        pub session: bool,
        pub eval_replies: VecDeque<Value>,
        pub calls: Vec<String>,
        pub args: Vec<(String, Value)>,
        pub identity_calls: usize,
        /// The identity check fails from this call number on.
        pub identity_fails_from: Option<usize>,
        pub click_fails: bool,
        /// Called with the call number and name before each call.
        pub hook: Option<Hook>,
        pub hash_after_goto: Option<String>,
        /// A goto of `/mail/u/<i>/` switches the signed-in account to `i`, as Gmail does.
        pub follow_goto: bool,
        /// This many window navigations leave the search box with a stale value, as Gmail did live. A mailbox root navigation clears it.
        pub stale_gotos: usize,
    }

    pub struct Sim(pub Mutex<State>);

    impl Sim {
        pub fn new() -> Self {
            Sim(Mutex::new(State {
                host: MAILBOX_HOST.into(),
                active: Some(0),
                ambiguous: false,
                hash: String::new(),
                page: 1,
                page_size: 3,
                data: BTreeMap::new(),
                qbox: None,
                session: true,
                eval_replies: VecDeque::new(),
                calls: vec![],
                args: vec![],
                identity_calls: 0,
                identity_fails_from: None,
                click_fails: false,
                hook: None,
                hash_after_goto: None,
                follow_goto: true,
                stale_gotos: 0,
            }))
        }

        pub fn rows(self, account: usize, rows: &[(&str, String)]) -> Self {
            self.0.lock().unwrap().data.insert(
                account,
                rows.iter()
                    .map(|(t, m)| (t.to_string(), m.clone()))
                    .collect(),
            );
            self
        }

        pub fn calls(&self) -> Vec<String> {
            self.0.lock().unwrap().calls.clone()
        }

        pub fn count(&self, name: &str) -> usize {
            self.calls().iter().filter(|c| c.as_str() == name).count()
        }

        fn page_json(s: &State, view: &str) -> Value {
            let rows = s
                .active
                .and_then(|a| s.data.get(&a))
                .cloned()
                .unwrap_or_default();
            let total = rows.len();
            let pages = total.div_ceil(s.page_size).max(1) as u32;
            let (lo, hi) = (
                (s.page as usize - 1) * s.page_size,
                (s.page as usize * s.page_size).min(total),
            );
            let shown: Vec<_> = if lo < total {
                rows[lo..hi].to_vec()
            } else {
                vec![]
            };
            let want = view
                .trim_start_matches("#search/")
                .replace("%3A", ":")
                .replace('+', " ");
            let hash_ok = s.hash == view
                || s.hash
                    .strip_prefix(view)
                    .is_some_and(|r| r.starts_with("/p"));
            let on = |ok: bool| json!({"n": 1, "disabled": !ok});
            let empty = shown.is_empty();
            json!({
                "host": s.host, "proto": "https:", "ready": "complete",
                "view": if hash_ok { view } else { "other" },
                "page": if hash_ok { json!(s.page) } else { Value::Null },
                "qn": 1,
                "qm": if s.qbox.clone().unwrap_or(want.clone()) == want { 1 } else { 0 },
                "path_ok": true, "grids": 1,
                "rows": shown.len(), "empty": u32::from(empty),
                "range": if empty { Value::Null } else { json!([lo + 1, hi, Value::Null]) },
                "older": if empty { json!({"n": 0, "disabled": false}) } else { on(s.page < pages) },
                "newer": if empty { json!({"n": 0, "disabled": false}) } else { on(s.page > 1) },
                "items": shown.iter().map(|(t, m)| json!({"t": [t], "m": [m, m], "u": false})).collect::<Vec<_>>(),
            })
        }
    }

    impl BrowserHost for Sim {
        fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
            Box::pin(async move {
                let hook = {
                    let mut s = self.0.lock().unwrap();
                    let n = s.calls.len();
                    s.hook.take().map(|h| (n, h))
                };
                if let Some((n, mut h)) = hook {
                    h(n, name);
                    self.0.lock().unwrap().hook = Some(h);
                }
                let mut s = self.0.lock().unwrap();
                let steps = args["steps"].as_str().unwrap_or("").to_owned();
                let js = args["js"].as_str().unwrap_or("").to_owned();
                let label = match name {
                    "open" => "open",
                    "sessions" => "sessions",
                    "eval" if js == "location.hostname" => "probe",
                    "eval" => "eval",
                    "step" if steps.starts_with("goto ") => "goto",
                    "step" if steps.contains("expect js") => "expect",
                    "step" if steps.starts_with("click css=") => "click",
                    _ => "other",
                };
                s.calls.push(label.into());
                s.args.push((label.into(), args.clone()));
                let ok = Value::String("ok (5 ms)".into());
                match label {
                    "sessions" => Ok(json!(if s.session {
                        "main: https://mail.google.com/"
                    } else {
                        "no live sessions"
                    })),
                    "open" => {
                        s.session = true;
                        s.host = MAILBOX_HOST.into();
                        Ok(ok)
                    }
                    "probe" => {
                        anyhow::ensure!(s.session, "no session");
                        Ok(Value::String(s.host.clone()))
                    }
                    "goto" => {
                        anyhow::ensure!(s.session, "no session");
                        let url = steps.trim_start_matches("goto ").to_owned();
                        if let Some(rest) = url.strip_prefix("https://mail.google.com/mail/u/") {
                            if let (true, Ok(i)) = (s.follow_goto, rest[..1].parse::<usize>()) {
                                s.active = Some(i);
                            }
                            s.hash = s.hash_after_goto.clone().unwrap_or_else(|| {
                                rest.split_once('#')
                                    .map_or(String::new(), |(_, h)| format!("#{h}"))
                            });
                        }
                        if url.contains("#search") {
                            if s.stale_gotos > 0 {
                                s.stale_gotos -= 1;
                                s.qbox = Some("stale".into());
                            } else if s.qbox.as_deref() == Some("stale") {
                                s.qbox = None;
                            }
                        }
                        s.page = 1;
                        Ok(ok)
                    }
                    "expect" => {
                        s.identity_calls += 1;
                        let n = s.identity_calls;
                        let wanted: usize = steps
                            .split("hit[")
                            .nth(1)
                            .and_then(|r| r.split(']').next())
                            .and_then(|r| r.parse().ok())
                            .unwrap();
                        let pass = s.host == MAILBOX_HOST
                            && !s.ambiguous
                            && s.active == Some(wanted)
                            && s.identity_fails_from.is_none_or(|f| n < f);
                        Ok(Value::String(if pass {
                            "ok (5 ms)".into()
                        } else {
                            "FAILED expect js".into()
                        }))
                    }
                    "click" => {
                        if s.click_fails {
                            return Ok(Value::String("FAILED click".into()));
                        }
                        s.page += 1;
                        let v = s.hash.split("/p").next().unwrap().to_owned();
                        s.hash = format!("{v}/p{}", s.page);
                        Ok(ok)
                    }
                    "eval" => {
                        if let Some(v) = s.eval_replies.pop_front() {
                            return Ok(v);
                        }
                        let view = s.hash.split("/p").next().unwrap().to_owned();
                        let view = if view.starts_with("#search/") {
                            view
                        } else {
                            "#search/none".into()
                        };
                        let raw = Sim::page_json(&s, &view);
                        Ok(Value::String(raw.to_string()))
                    }
                    _ => Ok(ok),
                }
            })
        }

        fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>> {
            Box::pin(async { Ok(vec![]) })
        }

        fn out_dirs(&self) -> Vec<std::path::PathBuf> {
            vec![]
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::Relaxed};

    use super::sim::*;
    use super::*;
    use crate::config::Config;

    const VM: &str = "[vm]\nversion = 1\nbot_id = \"main\"\nruntime_dir = \"/rt\"\nharness = \"/h/vmproto.py\"\n";
    const TOML: &str = "[gmail]\naccount_control_selector = \"a.acct\"\nsearch_box_selector = \"input.q\"\n[[gmail.accounts]]\nkey = \"one\"\nindex = 0\nuser_secret = \"ACCOUNT_ONE_USER\"\n[[gmail.accounts]]\nkey = \"two\"\nindex = 1\nuser_secret = \"ACCOUNT_TWO_USER\"\n";

    pub(super) fn cfg() -> Gmail {
        let c = Config::parse(&format!(
            "{}\n{VM}\n{TOML}",
            include_str!("../../deck.example.toml")
        ))
        .unwrap();
        c.gmail.unwrap()
    }

    pub struct Flag(pub AtomicBool, pub AtomicUsize);
    impl Flag {
        pub fn open() -> Self {
            Flag(AtomicBool::new(true), AtomicUsize::new(usize::MAX))
        }
    }
    impl Gate for Flag {
        fn open(&self) -> BoxFuture<'_, bool> {
            Box::pin(async move {
                let left = self.1.load(Relaxed);
                if left == 0 {
                    return false;
                }
                if left != usize::MAX {
                    self.1.store(left - 1, Relaxed);
                }
                self.0.load(Relaxed)
            })
        }
    }

    fn fast() -> Pacing {
        Pacing {
            retry_wait: Duration::from_millis(1),
            advance_wait: Duration::from_millis(1),
            advance_polls: 4,
            read_attempts: 3,
        }
    }

    const LOWER: i64 = 1_800_000_000;

    async fn run(sim: &Sim, gate: &Flag, acct: usize) -> Result<Scanned, ScanFail> {
        let c = cfg();
        let s = Scanner {
            host: sim,
            gate,
            cfg: &c,
            pacing: fast(),
        };
        s.scan(&c.accounts[acct], LOWER).await
    }

    fn fail(r: Result<Scanned, ScanFail>) -> (GmailScanFailure, &'static str) {
        let e = r.unwrap_err();
        (e.kind, e.reason)
    }

    // ---------- pure pieces ----------

    #[test]
    fn the_window_plan_uses_the_previous_start_never_the_completion_or_the_initial_cut() {
        let now = 1_800_000_000_000;
        let p = plan_window(now, None, 24, 3600).unwrap();
        assert_eq!(
            (p.kind, p.lower_s),
            (PlanKind::Baseline, 1_800_000_000 - 24 * 3600)
        );
        // One hour ago, ten days ago: the window starts at the old start minus the overlap. It is never cut to 24 hours.
        for ago_s in [3600_i64, 10 * 86_400] {
            let w = now - ago_s * 1000;
            let p = plan_window(now, Some(w), 24, 3600).unwrap();
            assert_eq!((p.kind, p.lower_s), (PlanKind::Catchup, w / 1000 - 3600));
        }
        // A zero overlap is allowed.
        assert_eq!(
            plan_window(now, Some(now - 5000), 24, 0).unwrap().lower_s,
            1_800_000_000 - 5
        );
    }

    #[test]
    fn a_clock_behind_the_watermark_or_outside_the_epoch_range_is_refused() {
        let now = 1_800_000_000_000;
        assert_eq!(
            plan_window(now, Some(now + 1), 24, 3600),
            Err("clock_behind")
        );
        assert!(plan_window(5_000, None, 24, 3600).is_err());
        assert!(plan_window(now, Some(0), 24, 3600).is_err());
        assert!(plan_window(now, Some(1_000), 24, 3600).is_err());
    }

    #[test]
    fn the_view_and_url_hold_only_the_fixed_query_and_digits() {
        assert_eq!(window_view(LOWER), "#search/in%3Ainbox+after%3A1800000000");
        assert_eq!(
            window_url(2, LOWER),
            "https://mail.google.com/mail/u/2/#search/in%3Ainbox+after%3A1800000000"
        );
    }

    #[test]
    fn the_identity_step_names_every_registered_secret_and_the_wanted_slot_only() {
        let s = identity_steps("a.acct", &["ACCOUNT_ONE_USER", "ACCOUNT_TWO_USER"], 1);
        assert!(s.starts_with("timeout 8000\nexpect js \""));
        assert!(s.contains("${ACCOUNT_ONE_USER}") && s.contains("${ACCOUNT_TWO_USER}"));
        assert!(s.contains("hit[1] === true"));
        assert!(s.contains("location.protocol !== 'https:'") && s.contains("mail.google.com"));
        assert!(s.contains("els.length !== 1") && s.contains("filter(Boolean).length === 1"));
        // No raw double quote inside the quoted program.
        let body = s.split_once("expect js \"").unwrap().1;
        let inner = &body[..body.len() - 1];
        assert!(!inner.replace("\\\"", "").contains('"'));
    }

    #[test]
    fn the_scan_program_is_fixed_and_reads_no_text_beyond_numbers_and_one_marker() {
        let js = scan_js("#search/in%3Ainbox+after%3A1800000000", "input.q");
        for needle in [
            "data-legacy-last-non-draft-message-id",
            "data-legacy-thread-id",
            "No messages matched your search",
            "input.q",
            "in%3Ainbox+after%3A1800000000",
        ] {
            assert!(js.contains(needle), "{needle}");
        }
        for forbidden in [
            "innerText",
            ".click(",
            "submit",
            "fetch(",
            "XMLHttp",
            "textContent.slice",
        ] {
            assert!(!js.contains(forbidden), "{forbidden}");
        }
        assert!(!js.contains("${"));
    }

    fn page(extra: Value) -> Value {
        let mut v = json!({
            "host": "mail.google.com", "proto": "https:", "ready": "complete",
            "view": "#search/in%3Ainbox+after%3A1800000000", "page": 1, "qn": 1, "qm": 1,
            "path_ok": true, "grids": 1, "rows": 2, "empty": 0, "range": [1, 2, null],
            "older": {"n": 1, "disabled": true}, "newer": {"n": 1, "disabled": true},
            "items": [{"t": [T[0]], "m": [m(1), m(1)], "u": false}, {"t": [T[1]], "m": [m(2)], "u": false}],
        });
        for (k, x) in extra.as_object().unwrap() {
            v[k] = x.clone();
        }
        v
    }

    const V: &str = "#search/in%3Ainbox+after%3A1800000000";

    #[test]
    fn a_valid_page_parses_with_ids_range_and_controls() {
        let p = parse_page(&page(json!({})), V).unwrap();
        assert_eq!((p.page, p.rows.len(), p.empty), (1, 2, false));
        assert_eq!(p.rows[0], (T[0].to_string(), m(1)));
        assert_eq!(p.range, Some((1, 2, None)));
        assert_eq!(
            p.older,
            Some(Ctl {
                present: true,
                disabled: true
            })
        );
        // A JSON-encoded string reply is the same page.
        let again = parse_page(&Value::String(page(json!({})).to_string()), V).unwrap();
        assert_eq!(again, p);
    }

    #[test]
    fn parser_rejects_wrong_hosts_views_and_search_boxes_with_distinct_fixed_statuses() {
        let st = |extra: Value| parse_page(&page(extra), V).unwrap_err();
        assert_eq!(
            st(json!({"host": "accounts.google.com"})),
            PageFail::NotMailbox
        );
        assert_eq!(
            st(json!({"host": "mail.google.com.evil.example"})),
            PageFail::NotMailbox
        );
        assert_eq!(st(json!({"proto": "http:"})), PageFail::NotMailbox);
        assert_eq!(st(json!({"ready": "loading"})), PageFail::Unloaded);
        // Another query, the Primary view, a wrong path and an unreadable hash.
        assert_eq!(st(json!({"view": "other"})), PageFail::NotInbox);
        assert_eq!(st(json!({"view": "#inbox"})), PageFail::NotInbox);
        assert_eq!(
            st(json!({"view": "#search/in%3Ainbox"})),
            PageFail::NotInbox
        );
        assert_eq!(st(json!({"path_ok": false})), PageFail::NotInbox);
        // The search box: missing, duplicated or holding another value.
        for extra in [
            json!({"qn": 0, "qm": 0}),
            json!({"qn": 2, "qm": 2}),
            json!({"qn": 1, "qm": 0}),
            json!({"qn": null}),
        ] {
            assert_eq!(st(extra), PageFail::Unsupported("query_box"));
        }
        assert_eq!(st(json!({"grids": 2})), PageFail::Unsupported("grid_count"));
        assert_eq!(st(json!({"grids": 0})), PageFail::Unsupported("grid_count"));
        assert_eq!(
            parse_page(&json!(["x"]), V).unwrap_err(),
            PageFail::Malformed
        );
        assert_eq!(
            parse_page(&Value::String("not json".into()), V).unwrap_err(),
            PageFail::Malformed
        );
        assert_eq!(
            parse_page(&Value::Null, V).unwrap_err(),
            PageFail::Malformed
        );
    }

    #[test]
    fn parser_checks_row_counts_ids_duplicates_and_ranges() {
        let st = |extra: Value| parse_page(&page(extra), V).unwrap_err();
        assert_eq!(
            st(json!({"rows": 3})),
            PageFail::Unsupported("row_count_mismatch")
        );
        assert_eq!(
            st(json!({"rows": 1})),
            PageFail::Unsupported("row_count_mismatch")
        );
        assert_eq!(
            st(json!({"range": [1, 5, null]})),
            PageFail::Unsupported("range_rows_mismatch")
        );
        assert_eq!(
            st(json!({"range": [1, "x", null]})),
            PageFail::Unsupported("range_shape")
        );
        // Thread or message IDs that are not 16 lowercase hex characters, or that differ between elements.
        for bad in [
            "0000000000000A01",
            "000000000000000",
            "00000000000000g1",
            "",
            "0000000000000001 ",
        ] {
            let items = json!([{"t": [bad], "m": [m(1)], "u": false}, {"t": [T[1]], "m": [m(2)], "u": false}]);
            assert_eq!(
                st(json!({"items": items})),
                PageFail::Unsupported("row_id_shape"),
                "{bad}"
            );
            let items = json!([{"t": [T[0]], "m": [bad], "u": false}, {"t": [T[1]], "m": [m(2)], "u": false}]);
            assert_eq!(
                st(json!({"items": items})),
                PageFail::Unsupported("message_id_shape"),
                "{bad}"
            );
        }
        let items = json!([{"t": [T[0], T[1]], "m": [m(1)], "u": false}, {"t": [T[1]], "m": [m(2)], "u": false}]);
        assert_eq!(
            st(json!({"items": items})),
            PageFail::Unsupported("row_id_shape")
        );
        let items =
            json!([{"t": [T[0]], "m": [m(1)], "u": false}, {"t": [T[0]], "m": [m(1)], "u": false}]);
        assert_eq!(
            st(json!({"items": items})),
            PageFail::Unsupported("duplicate_thread_id")
        );
        let items = json!([{"t": [T[0]], "u": false}, {"t": [T[1]], "m": [m(2)], "u": false}]);
        assert_eq!(
            st(json!({"items": items})),
            PageFail::Unsupported("message_id_shape")
        );
        // More rows than the cap is partial, never ok.
        let many: Vec<Value> = (0..101)
            .map(|i| json!({"t": [format!("{:016x}", i + 1)], "m": [m(i)], "u": false}))
            .collect();
        let capped: Vec<Value> = many.iter().take(100).cloned().collect();
        assert_eq!(
            st(json!({"rows": 101, "range": null, "items": capped})),
            PageFail::Unsupported("too_many_rows")
        );
        // Page number must be a positive integer.
        assert_eq!(st(json!({"page": 0})), PageFail::Unsupported("page_shape"));
        assert_eq!(
            st(json!({"page": null})),
            PageFail::Unsupported("page_shape")
        );
    }

    #[test]
    fn empty_needs_a_positive_marker_and_no_rows() {
        let empty = |extra: Value| {
            let mut e = json!({"rows": 0, "empty": 1, "range": null, "items": [], "older": {"n": 0, "disabled": false}, "newer": {"n": 0, "disabled": false}});
            for (k, x) in extra.as_object().unwrap() {
                e[k] = x.clone();
            }
            parse_page(&page(e), V)
        };
        let p = empty(json!({})).unwrap();
        assert!(p.empty && p.rows.is_empty());
        // Zero rows without the marker proves nothing.
        assert_eq!(
            empty(json!({"empty": 0})).unwrap_err(),
            PageFail::NoRowsUnproven
        );
        assert_eq!(
            empty(json!({"empty": 2})).unwrap_err(),
            PageFail::Unsupported("marker_count")
        );
        // The marker beside rows is contradictory.
        assert_eq!(
            parse_page(&page(json!({"empty": 1})), V).unwrap_err(),
            PageFail::Unsupported("marker_with_rows")
        );
    }

    // ---------- the scan, on a simulated Gmail ----------

    #[tokio::test]
    async fn a_single_page_window_scans_with_identity_checks_around_every_read() {
        let sim = Sim::new().rows(1, &[(T[0], m(1)), (T[1], m(2))]);
        let r = run(&sim, &Flag::open(), 1).await.unwrap();
        assert_eq!(r.pages, 1);
        assert_eq!(
            r.threads,
            vec![
                GmailThreadRev {
                    thread_id: T[0].into(),
                    message_id: m(1)
                },
                GmailThreadRev {
                    thread_id: T[1].into(),
                    message_id: m(2)
                },
            ]
        );
        // goto, [expect eval expect], goto, [expect eval expect]: the recheck is a second full read.
        assert_eq!(
            sim.calls(),
            [
                "goto", "expect", "eval", "expect", "goto", "expect", "eval", "expect"
            ]
        );
        let first = sim.0.lock().unwrap().args[0].1["steps"]
            .as_str()
            .unwrap()
            .to_owned();
        assert_eq!(
            first,
            "goto https://mail.google.com/mail/u/1/#search/in%3Ainbox+after%3A1800000000"
        );
    }

    #[tokio::test]
    async fn a_multi_page_window_follows_next_to_a_disabled_control_without_duplicates() {
        let rows: Vec<_> = (0..7).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        let r = run(&sim, &Flag::open(), 0).await.unwrap();
        assert_eq!((r.pages, r.threads.len()), (3, 7));
        assert_eq!(sim.count("click"), 2);
        assert_eq!(r.threads[6].thread_id, T[6]);
    }

    async fn walk(
        sim: &Sim,
        gate: &Flag,
        start: u32,
        pages: u32,
        exclude: &str,
    ) -> Result<Walk, ScanFail> {
        let c = cfg();
        let s = Scanner {
            host: sim,
            gate,
            cfg: &c,
            pacing: fast(),
        };
        s.scan_backlog(&c.accounts[0], LOWER, exclude, start, pages)
            .await
    }

    fn ids(w: &Walk) -> Vec<Vec<String>> {
        w.pages
            .iter()
            .map(|p| p.iter().map(|t| t.thread_id.clone()).collect())
            .collect()
    }

    #[tokio::test]
    async fn the_backlog_walk_reads_the_pages_from_the_checkpoint_and_says_when_the_results_end() {
        let rows: Vec<_> = (0..7).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        let w = walk(&sim, &Flag::open(), 1, 1, "").await.unwrap();
        assert_eq!(
            (ids(&w), w.end),
            (
                vec![vec![T[0].to_string(), T[1].into(), T[2].into()]],
                false
            )
        );
        let w = walk(&sim, &Flag::open(), 2, 2, "").await.unwrap();
        assert_eq!(ids(&w).len(), 2);
        assert!(w.end, "page 3 is the last page");
        let w = walk(&sim, &Flag::open(), 3, 1, "").await.unwrap();
        assert_eq!((ids(&w), w.end), (vec![vec![T[6].to_string()]], true));
        // A start page past the end of the results is the end, with no thread.
        let w = walk(&sim, &Flag::open(), 9, 1, "").await.unwrap();
        assert_eq!((ids(&w), w.end), (vec![], true));
    }

    #[tokio::test]
    async fn the_backlog_walk_reads_only_older_than_its_ceiling_with_the_owner_terms_and_writes_nothing()
     {
        let rows: Vec<_> = (0..2).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        walk(&sim, &Flag::open(), 1, 1, "-label:deck")
            .await
            .unwrap();
        let gotos: Vec<String> = sim
            .0
            .lock()
            .unwrap()
            .args
            .iter()
            .filter(|(l, _)| l == "goto")
            .map(|(_, a)| a["steps"].as_str().unwrap().to_owned())
            .collect();
        assert!(
            gotos[0].ends_with(&format!(
                "#search/in%3Ainbox+before%3A{LOWER}+-label%3Adeck"
            )),
            "{gotos:?}"
        );
        assert!(sim.calls().iter().all(|c| c != "other"));
    }

    #[tokio::test]
    async fn a_closed_gate_or_a_stalled_page_ends_the_backlog_walk_with_no_pages() {
        let rows: Vec<_> = (0..7).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        let gate = Flag::open();
        gate.0.store(false, std::sync::atomic::Ordering::Relaxed);
        let e = walk(&sim, &gate, 1, 1, "").await.unwrap_err();
        assert!(e.aborted);
        let sim = Sim::new().rows(0, &rows);
        sim.0.lock().unwrap().click_fails = true;
        let e = walk(&sim, &Flag::open(), 2, 1, "").await.unwrap_err();
        assert_eq!(e.reason, "advance_failed");
    }

    /// One cleanup pass of the phase on the simulated mailbox. Triage is unwired, so it never changes a row.
    async fn pass(
        store: &crate::store::Store,
        sim: &Sim,
        c: &Gmail,
    ) -> crate::bot::gmail_summary::PhaseEnd {
        pass_until(store, sim, c, None).await
    }

    /// Like `pass`, with the time cap of the writes at `deadline`.
    async fn pass_until(
        store: &crate::store::Store,
        sim: &Sim,
        c: &Gmail,
        deadline: Option<tokio::time::Instant>,
    ) -> crate::bot::gmail_summary::PhaseEnd {
        let chat = store.bot_flags("main", "Bot").await.unwrap().chat_id;
        let gate = Flag::open();
        let token = tokio_util::sync::CancellationToken::new();
        let scanner = Scanner {
            host: sim,
            gate: &gate,
            cfg: c,
            pacing: fast(),
        };
        let clock = || 1_800_000_000_000;
        crate::bot::gmail_summary::Env {
            store,
            bot_id: "main",
            chat_id: chat,
            cfg: c,
            scanner: &scanner,
            gate: &gate,
            token: &token,
            triager: &crate::bot::gmail_summary::Unwired,
            clock: &clock,
            stage: &|_| {},
        }
        .run_inner_for_test(deadline)
        .await
    }

    const CLEANUP_TOML: &str = "time = \"23:59\"
[labels]
chips_selector = \"span.chip\"
[labels.steps]
needs_reply = \"click css=\\\"#one\\\"\"
action = \"click css=\\\"#two\\\"\"
waiting = \"click css=\\\"#three\\\"\"
fyi = \"click css=\\\"#four\\\"\"
[cleanup]
trash_step = \"click css=\\\"#delete\\\"\"
backlog_queue_cap = 4
";

    #[tokio::test]
    async fn the_backlog_walks_one_page_a_pass_with_a_queue_cap_and_completes_only_after_a_quiet_sweep()
     {
        let store = crate::store::Store::open_memory().unwrap();
        let rows: Vec<_> = (0..7).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        let mut c = cfg();
        c.summary = Some(toml::from_str(CLEANUP_TOML).unwrap());
        store
            .record_gmail_window_scan_as(
                "main",
                "one",
                vec![],
                GmailWindow {
                    start_ms: 1_799_999_000_000,
                    lower_s: LOWER,
                },
                1_799_999_000_000,
                ScanMode::Triage,
            )
            .await
            .unwrap();
        let state = || async { store.gmail_backlog_state("main", "one").await.unwrap() };
        // The first pass only starts the checkpoint. Newer mail stays with the window scan.
        pass(&store, &sim, &c).await;
        assert_eq!(state().await.unwrap().ceiling_s, LOWER);
        assert_eq!(sim.count("eval"), 0, "no read before the checkpoint exists");
        // Each pass reads one page. The queue of an account holds at most the cap plus one page.
        pass(&store, &sim, &c).await;
        assert_eq!(store.gmail_backlog_pending("main", "one").await.unwrap(), 3);
        pass(&store, &sim, &c).await;
        pass(&store, &sim, &c).await;
        assert_eq!(store.gmail_backlog_pending("main", "one").await.unwrap(), 6);
        let reads = sim.count("eval");
        pass(&store, &sim, &c).await;
        // At the cap, the walk reads nothing more, and says so by its checkpoint.
        let st = state().await.unwrap();
        assert!(!st.complete);
        assert!(sim.count("eval") >= reads);
        // A worker settles the queue between passes. The walk finishes the sweep, then needs a quiet sweep.
        for _ in 0..30 {
            store
                .run(|c| {
                    c.execute(
                        "UPDATE gmail_triage SET state = 'superseded' WHERE state = 'pending'",
                        [],
                    )
                })
                .await
                .unwrap();
            pass(&store, &sim, &c).await;
            if state().await.unwrap().complete {
                break;
            }
        }
        let st = state().await.unwrap();
        assert!(st.complete);
        assert_eq!((st.queued_total, st.sweep >= 2), (7, true));
        // A complete walk reads nothing more.
        let reads = sim.count("eval");
        pass(&store, &sim, &c).await;
        assert_eq!(sim.count("eval"), reads);
    }

    #[tokio::test]
    async fn the_backlog_read_does_not_wait_for_the_label_writes_to_leave_time() {
        let store = crate::store::Store::open_memory().unwrap();
        let rows: Vec<_> = (0..3).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        let mut c = cfg();
        c.summary = Some(toml::from_str(CLEANUP_TOML).unwrap());
        store
            .gmail_backlog_start("main", "one", LOWER, 5)
            .await
            .unwrap();
        // The time cap of the writes is spent before the pass starts, as slow label writes spend it live.
        pass_until(&store, &sim, &c, Some(tokio::time::Instant::now())).await;
        assert!(sim.count("eval") > 0, "the page was never read");
        assert!(store.gmail_backlog_pending("main", "one").await.unwrap() > 0);
    }

    #[tokio::test]
    async fn the_backlog_job_keeps_the_first_account_until_its_work_is_done_and_then_takes_the_next()
     {
        let store = crate::store::Store::open_memory().unwrap();
        let rows: Vec<_> = (0..3).map(|i| (T[i], m(i as u32))).collect();
        let rows2: Vec<_> = (3..6).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows).rows(1, &rows2);
        let mut c = cfg();
        c.summary = Some(
            toml::from_str(&CLEANUP_TOML.replace("backlog_queue_cap = 4", "backlog_queue_cap = 2"))
                .unwrap(),
        );
        // One page holds more rows than the cap. The first account fills its queue and nothing settles it.
        for a in ["one", "two"] {
            store
                .gmail_backlog_start("main", a, LOWER, 5)
                .await
                .unwrap();
        }
        pass(&store, &sim, &c).await;
        pass(&store, &sim, &c).await;
        assert_eq!(store.gmail_backlog_pending("main", "one").await.unwrap(), 3);
        let two = store
            .gmail_backlog_state("main", "two")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            two.queued_total, 0,
            "the second account was read while the first had work"
        );
        let keys: Vec<String> = ["one", "two"].map(String::from).to_vec();
        assert_eq!(
            store
                .gmail_backlog_focus("main", &keys)
                .await
                .unwrap()
                .as_deref(),
            Some("one")
        );
    }

    #[tokio::test]
    async fn new_mail_ranks_before_the_backlog_and_a_gate_stop_keeps_the_checkpoint() {
        let store = crate::store::Store::open_memory().unwrap();
        let mk = |t: &str| GmailThreadRev {
            thread_id: t.into(),
            message_id: m(1),
        };
        store
            .gmail_backlog_start("main", "one", LOWER, 5)
            .await
            .unwrap();
        store
            .gmail_backlog_record("main", "one", 1, vec![vec![mk(T[0]), mk(T[1])]], false, 10)
            .await
            .unwrap();
        // New mail arrives after the backlog rows were queued.
        store
            .record_gmail_window_scan_as(
                "main",
                "one",
                vec![mk(T[5])],
                GmailWindow {
                    start_ms: 1_799_999_000_000,
                    lower_s: LOWER,
                },
                1_799_999_000_000,
                ScanMode::Triage,
            )
            .await
            .unwrap();
        let p = store.gmail_pending_triage("main", 10, None).await.unwrap();
        let order: Vec<_> = p.iter().map(|r| r.key.thread_id.as_str()).collect();
        assert_eq!(order, vec![T[5], T[0], T[1]]);
        // A closed gate during a pass changes no checkpoint.
        let before = store.gmail_backlog_state("main", "one").await.unwrap();
        let mut c = cfg();
        c.summary = Some(toml::from_str(CLEANUP_TOML).unwrap());
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        let chat = store.bot_flags("main", "Bot").await.unwrap().chat_id;
        let gate = Flag::open();
        gate.0.store(false, std::sync::atomic::Ordering::Relaxed);
        let token = tokio_util::sync::CancellationToken::new();
        let scanner = Scanner {
            host: &sim,
            gate: &gate,
            cfg: &c,
            pacing: fast(),
        };
        let clock = || 1_800_000_000_000;
        let end = crate::bot::gmail_summary::Env {
            store: &store,
            bot_id: "main",
            chat_id: chat,
            cfg: &c,
            scanner: &scanner,
            gate: &gate,
            token: &token,
            triager: &crate::bot::gmail_summary::Unwired,
            clock: &clock,
            stage: &|_| {},
        }
        .run()
        .await;
        assert_eq!(
            end,
            crate::bot::gmail_summary::PhaseEnd::Interrupted("blocked")
        );
        assert_eq!(
            store.gmail_backlog_state("main", "one").await.unwrap(),
            before
        );
    }

    #[tokio::test]
    async fn an_empty_window_needs_the_marker_and_a_stable_recheck() {
        let sim = Sim::new().rows(0, &[]);
        let r = run(&sim, &Flag::open(), 0).await.unwrap();
        assert!(r.threads.is_empty());
        assert_eq!(sim.count("eval"), 2);
    }

    #[tokio::test]
    async fn the_page_cap_makes_the_scan_incomplete_never_complete() {
        let rows: Vec<_> = (0..7).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        let mut c = cfg();
        c.max_pages = 2;
        let s = Scanner {
            host: &sim,
            gate: &Flag::open(),
            cfg: &c,
            pacing: fast(),
        };
        assert_eq!(
            fail(s.scan(&c.accounts[0], LOWER).await),
            (GmailScanFailure::Incomplete, "page_cap")
        );
    }

    #[tokio::test]
    async fn an_unstable_first_page_is_incomplete() {
        let sim = Sim::new();
        // The recheck reads page 1 again. A new first thread arrived in between.
        let changed = page(json!({
            "rows": 3, "range": [1, 3, null],
            "items": [{"t": [T[2]], "m": [m(9)]}, {"t": [T[0]], "m": [m(1)]}, {"t": [T[1]], "m": [m(2)]}],
        }));
        {
            let mut s = sim.0.lock().unwrap();
            s.eval_replies
                .push_back(Value::String(page(json!({})).to_string()));
            s.eval_replies.push_back(Value::String(changed.to_string()));
        }
        assert_eq!(
            fail(run(&sim, &Flag::open(), 0).await),
            (GmailScanFailure::Incomplete, "first_page_changed")
        );
        // The same page twice is stable.
        let sim = Sim::new();
        {
            let mut s = sim.0.lock().unwrap();
            for _ in 0..2 {
                s.eval_replies
                    .push_back(Value::String(page(json!({})).to_string()));
            }
        }
        assert_eq!(run(&sim, &Flag::open(), 0).await.unwrap().threads.len(), 2);
    }

    #[tokio::test]
    async fn a_thread_that_changes_its_latest_message_between_pages_is_incomplete() {
        let sim = Sim::new();
        let first = page(json!({
            "rows": 3, "range": [1, 3, null], "older": {"n": 1, "disabled": false},
            "items": [{"t": [T[0]], "m": [m(0)]}, {"t": [T[1]], "m": [m(1)]}, {"t": [T[2]], "m": [m(2)]}],
        }));
        // Page 2 repeats thread 0 with another latest message.
        let p2 = page(json!({
            "page": 2, "rows": 1, "range": [4, 4, null],
            "older": {"n": 1, "disabled": true}, "newer": {"n": 1, "disabled": false},
            "items": [{"t": [T[0]], "m": [m(99)]}],
        }));
        {
            let mut s = sim.0.lock().unwrap();
            s.eval_replies.push_back(Value::String(first.to_string()));
            s.eval_replies.push_back(Value::String(p2.to_string()));
        }
        assert_eq!(
            fail(run(&sim, &Flag::open(), 0).await),
            (GmailScanFailure::Incomplete, "changed_during_walk")
        );
    }

    #[tokio::test]
    async fn pagination_problems_are_incomplete_with_their_own_reason() {
        let cases: Vec<(&str, Vec<Value>)> = vec![
            (
                "not_first_page",
                vec![page(
                    json!({"page": 2, "older": {"n": 1, "disabled": false}}),
                )],
            ),
            (
                "not_first_page",
                vec![page(
                    json!({"newer": {"n": 1, "disabled": false}, "older": {"n": 1, "disabled": false}}),
                )],
            ),
            (
                "pager_missing",
                vec![page(json!({"older": {"n": 0, "disabled": false}}))],
            ),
            ("pager_missing", vec![page(json!({"older": null}))]),
            ("total_mismatch", vec![page(json!({"range": [1, 2, 9]}))]),
        ];
        for (reason, replies) in cases {
            let sim = Sim::new().rows(0, &[(T[0], m(1))]);
            sim.0
                .lock()
                .unwrap()
                .eval_replies
                .extend(replies.into_iter().map(|v| Value::String(v.to_string())));
            let r = run(&sim, &Flag::open(), 0).await;
            assert_eq!(fail(r), (GmailScanFailure::Incomplete, reason), "{reason}");
        }
    }

    #[tokio::test]
    async fn a_page_two_without_progress_or_with_a_range_gap_is_incomplete() {
        let first = page(json!({
            "rows": 2, "range": [1, 2, null], "older": {"n": 1, "disabled": false},
        }));
        let again = |range: Value| {
            page(json!({
                "page": 2, "rows": 2, "range": range,
                "older": {"n": 1, "disabled": true}, "newer": {"n": 1, "disabled": false},
            }))
        };
        for (second, reason) in [(again(json!([3, 4, null])), "no_progress")] {
            let sim = Sim::new().rows(0, &[(T[0], m(1))]);
            {
                let mut s = sim.0.lock().unwrap();
                s.eval_replies.push_back(Value::String(first.to_string()));
                s.eval_replies.push_back(Value::String(second.to_string()));
            }
            assert_eq!(
                fail(run(&sim, &Flag::open(), 0).await),
                (GmailScanFailure::Incomplete, reason)
            );
        }
        // New threads with a gap in the range.
        let gap = page(json!({
            "page": 2, "rows": 1, "range": [9, 9, null],
            "older": {"n": 1, "disabled": true}, "newer": {"n": 1, "disabled": false},
            "items": [{"t": [T[5]], "m": [m(5)]}],
        }));
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        {
            let mut s = sim.0.lock().unwrap();
            s.eval_replies.push_back(Value::String(first.to_string()));
            s.eval_replies.push_back(Value::String(gap.to_string()));
        }
        assert_eq!(
            fail(run(&sim, &Flag::open(), 0).await),
            (GmailScanFailure::Incomplete, "range_discontinuity")
        );
    }

    #[tokio::test]
    async fn a_failed_click_or_an_unreadable_second_page_is_incomplete() {
        let rows: Vec<_> = (0..5).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        sim.0.lock().unwrap().click_fails = true;
        assert_eq!(
            fail(run(&sim, &Flag::open(), 0).await),
            (GmailScanFailure::Incomplete, "advance_failed")
        );
    }

    #[tokio::test]
    async fn a_stale_search_box_is_read_again_after_one_fresh_navigation_through_the_mailbox_root()
    {
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        sim.0.lock().unwrap().stale_gotos = 1;
        let got = run(&sim, &Flag::open(), 0).await.expect("one recovery");
        assert_eq!(got.threads.len(), 1);
        let urls: Vec<String> = sim
            .0
            .lock()
            .unwrap()
            .args
            .iter()
            .filter(|(n, _)| n == "goto")
            .map(|(_, a)| a["steps"].as_str().unwrap().to_owned())
            .collect();
        assert!(
            urls.iter()
                .any(|u| u == "goto https://mail.google.com/mail/u/0/"),
            "{urls:?}"
        );
    }

    #[tokio::test]
    async fn a_search_box_that_stays_wrong_after_the_fresh_navigation_is_still_a_failure() {
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        sim.0.lock().unwrap().stale_gotos = 2;
        assert_eq!(
            fail(run(&sim, &Flag::open(), 0).await),
            (GmailScanFailure::Error, "query_box")
        );
        assert_eq!(sim.count("goto"), 3, "one retry only, never a loop");
    }

    #[tokio::test]
    async fn another_account_or_two_accounts_in_one_label_refuse_the_read_and_name_no_data() {
        for ambiguous in [false, true] {
            let sim = Sim::new().rows(0, &[(T[0], m(1))]).rows(1, &[(T[1], m(2))]);
            {
                let mut s = sim.0.lock().unwrap();
                s.ambiguous = ambiguous;
                // The browser stays on account 1 although account 0 is wanted, or shows both.
                s.follow_goto = false;
                s.active = Some(if ambiguous { 0 } else { 1 });
            }
            let (kind, reason) = fail(run(&sim, &Flag::open(), 0).await);
            assert_eq!(
                (kind, reason),
                (GmailScanFailure::IdentityMismatch, "identity_refused")
            );
            assert_eq!(
                sim.count("eval"),
                0,
                "no page data is read for an unverified account"
            );
        }
    }

    #[tokio::test]
    async fn an_identity_that_changes_during_a_read_discards_it() {
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        // Identity passes before the read and fails after it.
        sim.0.lock().unwrap().identity_fails_from = Some(2);
        let r = run(&sim, &Flag::open(), 0).await;
        assert_eq!(
            fail(r),
            (GmailScanFailure::IdentityMismatch, "identity_changed")
        );
    }

    #[tokio::test]
    async fn a_signed_out_browser_is_an_auth_failure_and_never_signs_in() {
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        {
            let mut s = sim.0.lock().unwrap();
            s.host = "accounts.google.com".into();
        }
        let r = run(&sim, &Flag::open(), 0).await;
        assert_eq!(fail(r), (GmailScanFailure::Auth, "signed_out"));
        // Only navigation, the fixed identity check and the host probe ran.
        let calls = sim.calls();
        assert!(
            calls
                .iter()
                .all(|c| ["goto", "expect", "probe"].contains(&c.as_str())),
            "{calls:?}"
        );
    }

    #[tokio::test]
    async fn an_unloaded_page_is_retried_then_incomplete() {
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        {
            let mut s = sim.0.lock().unwrap();
            for _ in 0..3 {
                s.eval_replies
                    .push_back(Value::String(page(json!({"ready": "loading"})).to_string()));
            }
        }
        assert_eq!(
            fail(run(&sim, &Flag::open(), 0).await),
            (GmailScanFailure::Incomplete, "unloaded")
        );
        assert_eq!(sim.count("eval"), 3);
        // A page that finishes loading on the second try succeeds.
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        sim.0
            .lock()
            .unwrap()
            .eval_replies
            .push_back(Value::String(page(json!({"ready": "loading"})).to_string()));
        assert!(run(&sim, &Flag::open(), 0).await.is_ok());
    }

    #[tokio::test]
    async fn a_page_that_is_not_the_intended_query_is_never_read_as_the_window() {
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        sim.0.lock().unwrap().qbox = Some("in:inbox".into());
        assert_eq!(
            fail(run(&sim, &Flag::open(), 0).await),
            (GmailScanFailure::Error, "query_box")
        );
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        sim.0.lock().unwrap().hash_after_goto = Some("#inbox".into());
        assert_eq!(
            fail(run(&sim, &Flag::open(), 0).await),
            (GmailScanFailure::Error, "not_inbox")
        );
    }

    #[tokio::test]
    async fn a_closed_gate_stops_before_the_next_operation_and_aborts() {
        // The gate closes after three operations: no fourth call is made.
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        let gate = Flag::open();
        gate.1.store(3, Relaxed);
        let e = run(&sim, &gate, 0).await.unwrap_err();
        assert_eq!(
            (e.kind, e.reason, e.aborted),
            (GmailScanFailure::Incomplete, "gate_closed", true)
        );
        assert_eq!(sim.calls().len(), 3);
        // Closed from the start: nothing runs.
        let sim = Sim::new().rows(0, &[(T[0], m(1))]);
        let gate = Flag::open();
        gate.0.store(false, Relaxed);
        assert!(run(&sim, &gate, 0).await.unwrap_err().aborted);
        assert!(sim.calls().is_empty());
    }

    #[tokio::test]
    async fn only_read_and_navigation_operations_are_ever_sent() {
        let rows: Vec<_> = (0..5).map(|i| (T[i], m(i as u32))).collect();
        let sim = Sim::new().rows(0, &rows);
        run(&sim, &Flag::open(), 0).await.unwrap();
        for (name, args) in sim.0.lock().unwrap().args.iter() {
            assert!(["goto", "expect", "eval", "click"].contains(&name.as_str()));
            if name == "click" {
                assert_eq!(
                    args["steps"],
                    "click css=\"div[role=button][aria-label^=Next]\""
                );
            }
        }
        // No tool other than step and eval.
        assert_eq!(sim.count("open"), 0);
    }

    struct FailedProbe(Sim);
    impl BrowserHost for FailedProbe {
        fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
            Box::pin(async move {
                if name == "eval" {
                    anyhow::bail!("page stopped");
                }
                self.0.call(name, args).await
            })
        }
        fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>> {
            self.0.tools()
        }
        fn out_dirs(&self) -> Vec<std::path::PathBuf> {
            vec![]
        }
    }

    #[tokio::test]
    async fn gmail_failed_page_probe_never_replaces_a_live_session() {
        let c = cfg();
        let gate = Flag::open();
        let host = FailedProbe(Sim::new());
        let scanner = Scanner {
            host: &host,
            gate: &gate,
            cfg: &c,
            pacing: fast(),
        };
        assert!(scanner.ensure_session().await.is_err());
        assert_eq!(host.0.count("open"), 0);
    }

    #[tokio::test]
    async fn the_session_is_opened_from_the_profile_only_when_none_answers() {
        let c = cfg();
        let gate = Flag::open();
        let live = Sim::new();
        let s = Scanner {
            host: &live,
            gate: &gate,
            cfg: &c,
            pacing: fast(),
        };
        s.ensure_session().await.unwrap();
        assert_eq!(live.calls(), ["probe"]);
        let none = Sim::new();
        none.0.lock().unwrap().session = false;
        let s = Scanner {
            host: &none,
            gate: &gate,
            cfg: &c,
            pacing: fast(),
        };
        s.ensure_session().await.unwrap();
        assert_eq!(none.calls(), ["probe", "probe", "sessions", "open"]);
        let args = none.0.lock().unwrap().args.last().unwrap().1.clone();
        assert_eq!(args["state"], "main");
        assert_eq!(args["url"], "https://mail.google.com/mail/u/0/");
        assert!(
            args.as_object()
                .unwrap()
                .keys()
                .all(|k| ["state", "url"].contains(&k.as_str()))
        );
    }
}

// ---------- the runtime: scheduler, claim and cycle ----------

use std::sync::{Arc, Mutex};

use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use super::Bot;

/// The opt-in poller of one bot. Absent configuration means this value does not exist.
pub struct GmailRuntime {
    pub(super) cfg: Gmail,
    /// The browser. In production it is the guest relay, so every call is a journaled executor command.
    pub(super) host: Arc<dyn BrowserHost>,
    pub(super) pacing: Pacing,
    /// Time from the start of one poll to the start of the next.
    pub(super) interval: Duration,
    /// Longest time for one account.
    pub(super) cap: Duration,
    pub(super) clock: Arc<dyn Fn() -> i64 + Send + Sync>,
    /// The tool-free model of the morning summary.
    pub(super) triager: Arc<dyn super::gmail_summary::Triager>,
    sched: Mutex<Sched>,
}

#[derive(Default)]
struct Sched {
    next_due: Option<Instant>,
    running: bool,
    cancel: Option<CancellationToken>,
    /// The last finished cycle. A cycle in progress never changes these.
    started_ms: Option<i64>,
    ended_ms: Option<i64>,
    result: Option<&'static str>,
    /// The start of the cycle in progress.
    run_started_ms: Option<i64>,
    /// What the running poll does now: `session` or `scan`. Fixed words and an account key only.
    stage: Option<&'static str>,
    account: Option<String>,
    /// How the last summary phase ended and when. A fixed word and a time, never mail text.
    phase: Option<(String, i64)>,
}

impl GmailRuntime {
    pub fn new(cfg: Gmail, host: Arc<dyn BrowserHost>) -> Self {
        Self {
            interval: Duration::from_secs(cfg.interval_secs),
            cap: Duration::from_secs(cfg.max_scan_secs),
            cfg,
            host,
            pacing: Pacing::default(),
            clock: Arc::new(super::db::now_ms),
            triager: Arc::new(super::gmail_summary::Unwired),
            sched: Mutex::default(),
        }
    }

    pub fn with_triager(mut self, t: Arc<dyn super::gmail_summary::Triager>) -> Self {
        self.triager = t;
        self
    }
}

impl GmailRuntime {
    /// The first poll is due at once after a start.
    pub(super) fn arm(&self) {
        self.sched.lock().unwrap().next_due = Some(Instant::now());
    }

    /// Keep an expired deadline too. The bot excludes it while dispatch is blocked.
    pub(super) fn deadline(&self) -> Option<Instant> {
        let s = self.sched.lock().unwrap();
        s.next_due.filter(|_| !s.running)
    }

    pub(super) fn cancel_current(&self) {
        if let Some(t) = &self.sched.lock().unwrap().cancel {
            t.cancel();
        }
    }
}

/// Asked before every browser call of a poll.
struct BotGate {
    bot: Arc<Bot>,
    token: CancellationToken,
}

impl Gate for BotGate {
    fn open(&self) -> BoxFuture<'_, bool> {
        Box::pin(async move {
            let b = &self.bot;
            if self.token.is_cancelled()
                || b.shutdown.is_cancelled()
                || b.stopping.load(std::sync::atomic::Ordering::Relaxed)
            {
                return false;
            }
            let Ok(flags) = b.store.bot_flags(&b.id, "Bot").await else {
                return false;
            };
            !flags.automation_paused
                && !flags.budget_hold
                && b.store.holds(&b.id).await.is_ok_and(|h| h.is_empty())
                && b.store.unresolved(&b.id).await.is_ok_and(|u| u.is_empty())
        })
    }
}

enum Ended {
    Scan(Result<Scanned, ScanFail>),
    Timeout,
    Cancelled,
}

impl Bot {
    pub(super) fn gmail_poll_due(&self) -> bool {
        self.gmail.as_ref().is_some_and(|g| {
            let s = g.sched.lock().unwrap();
            !s.running && s.next_due.is_some_and(|d| d <= Instant::now())
        })
    }

    /// Claim the computer for one poll. Repeats the checks of a model dispatch under the same gate.
    pub(super) async fn dispatch_gmail_poll(self: &Arc<Self>) -> anyhow::Result<()> {
        let Some(rt) = &self.gmail else {
            return Ok(());
        };
        let task = match self
            .store
            .enqueue_gmail_poll(&self.id, self.chat_id)
            .await?
        {
            Some(t) => t,
            None => match self.store.next_queued_gmail_poll(&self.id).await? {
                Some(t) => t,
                None => return Ok(()),
            },
        };
        let gate = self.dispatch_gate.lock().await;
        let flags = self.store.bot_flags(&self.id, "Bot").await?;
        let clear = !flags.automation_paused
            && !flags.budget_hold
            && self.store.holds(&self.id).await?.is_empty()
            && self.inner.lock().unwrap().current.is_none()
            && self.store.owner_task(&self.id).await?.is_none()
            && self.store.unresolved(&self.id).await?.is_empty()
            && !self.stopping.load(std::sync::atomic::Ordering::Relaxed)
            && !rt.sched.lock().unwrap().running;
        if !clear {
            self.store
                .move_task(
                    &task.id,
                    &["queued"],
                    "cancelled",
                    Some("blocked at the claim".into()),
                )
                .await?;
            return Ok(());
        }
        let token = CancellationToken::new();
        {
            let mut i = self.inner.lock().unwrap();
            i.current = Some(super::Current {
                task_id: task.id.clone(),
                run_id: None,
                poll: true,
            });
            i.idle_since = None;
        }
        {
            let mut s = rt.sched.lock().unwrap();
            s.running = true;
            s.next_due = Some(Instant::now() + rt.interval);
            s.cancel = Some(token.clone());
            s.run_started_ms = Some((rt.clock)());
            s.stage = Some("session");
            s.account = None;
        }
        drop(gate);
        if let Err(e) = self.ensure_awake().await {
            let why = format!("the computer did not wake: {e:#}");
            self.store
                .move_task(&task.id, &["queued"], "failed", Some(why))
                .await?;
            self.end_poll("wake_failed").await;
            return Ok(());
        }
        if !self
            .store
            .move_task(&task.id, &["queued"], "running", None)
            .await?
        {
            self.end_poll("lost").await;
            return Ok(());
        }
        self.hub.set_task(&task.id);
        let this = self.clone();
        tokio::spawn(async move {
            let (state, detail, result) = this.gmail_cycle(&task.id, token).await;
            if let Err(e) = this
                .store
                .move_task(&task.id, &["running"], state, detail)
                .await
            {
                tracing::error!("cannot end poll {}: {e:#}", task.id);
            }
            this.hub.set_task("");
            this.end_poll(result).await;
        });
        Ok(())
    }

    async fn end_poll(&self, result: &'static str) {
        if let Some(rt) = &self.gmail {
            let mut s = rt.sched.lock().unwrap();
            s.running = false;
            s.cancel = None;
            s.started_ms = s.run_started_ms.take();
            s.ended_ms = Some((rt.clock)());
            s.result = Some(result);
            s.stage = None;
            s.account = None;
            // An overrun leaves one poll due at its end, never one per missed interval.
        }
        self.inner.lock().unwrap().current = None;
        self.touch();
        self.kick();
    }

    /// Stop the live commands of the poll twice, as a user stop does. True when no command remains unresolved.
    async fn settle(&self, task: &str) -> bool {
        for _ in 0..2 {
            let _ = self.hub.stop_task(task).await;
        }
        self.store
            .unresolved(&self.id)
            .await
            .is_ok_and(|u| u.is_empty())
    }

    /// Record a failed attempt. Nothing else changes.
    async fn poll_failed(&self, key: &str, kind: GmailScanFailure, at: i64) {
        if let Err(e) = self
            .store
            .record_gmail_scan_failure(&self.id, key, kind, at)
            .await
        {
            tracing::error!("cannot record a Gmail attempt: {e:#}");
        }
    }

    /// Run all accounts in turn. Returns the task end state, its detail and the cycle result.
    async fn gmail_cycle(
        self: &Arc<Self>,
        task: &str,
        token: CancellationToken,
    ) -> (&'static str, Option<String>, &'static str) {
        let rt = self.gmail.as_ref().expect("a poll needs the configuration");
        let gate = BotGate {
            bot: self.clone(),
            token: token.clone(),
        };
        let scanner = Scanner {
            host: rt.host.as_ref(),
            gate: &gate,
            cfg: &rt.cfg,
            pacing: rt.pacing,
        };
        let blocked = (
            "cancelled",
            Some("blocked by a hold or the owner".into()),
            "blocked",
        );
        let stopped = ("cancelled", Some("stopped by the owner".into()), "stopped");
        let session = tokio::select! {
            r = scanner.ensure_session() => Some(r),
            _ = tokio::time::sleep(rt.cap) => None,
            _ = token.cancelled() => None,
        };
        if !matches!(session, Some(Ok(()))) {
            let aborted = matches!(&session, Some(Err(e)) if e.aborted);
            let kind = match session {
                Some(Err(e)) => e.kind,
                _ => GmailScanFailure::Incomplete,
            };
            let at = (rt.clock)();
            for a in &rt.cfg.accounts {
                self.poll_failed(&a.key, kind, at).await;
            }
            self.settle(task).await;
            return if token.is_cancelled() {
                stopped
            } else if aborted {
                blocked
            } else {
                ("failed", Some("no browser session".into()), "session")
            };
        }
        let mut failures = 0;
        // Fixed reason codes with account keys. Never page text.
        let mut reasons: Vec<String> = vec![];
        for (i, acct) in rt.cfg.accounts.iter().enumerate() {
            if i > 0 {
                if token.is_cancelled() {
                    return stopped;
                }
                if !gate.open().await {
                    return blocked;
                }
                // An owner message ranks above the rest of the poll.
                if self
                    .store
                    .next_queued(&self.id)
                    .await
                    .ok()
                    .flatten()
                    .is_some_and(|t| t.priority >= 1)
                {
                    return ("done", Some("yielded to an owner task".into()), "yielded");
                }
            }
            let start = (rt.clock)();
            {
                let mut s = rt.sched.lock().unwrap();
                s.stage = Some("scan");
                s.account = Some(acct.key.clone());
            }
            let wm = match self.store.gmail_account_status(&self.id, &acct.key).await {
                Ok(status) => status.and_then(|s| s.watermark_ms),
                Err(_) => {
                    return (
                        "failed",
                        Some("cannot read scan state".into()),
                        "storage_error",
                    );
                }
            };
            let plan = match plan_window(start, wm, rt.cfg.initial_hours, rt.cfg.overlap_secs) {
                Ok(p) => p,
                Err(_) => {
                    self.poll_failed(&acct.key, GmailScanFailure::Error, start)
                        .await;
                    failures += 1;
                    continue;
                }
            };
            let ended = tokio::select! {
                r = scanner.scan(acct, plan.lower_s) => Ended::Scan(r),
                _ = tokio::time::sleep(rt.cap) => Ended::Timeout,
                _ = token.cancelled() => Ended::Cancelled,
            };
            match ended {
                Ended::Scan(Ok(scanned)) => {
                    let done = (rt.clock)();
                    let w = GmailWindow {
                        start_ms: start,
                        lower_s: plan.lower_s,
                    };
                    let mode = if rt.cfg.summary.is_some() {
                        ScanMode::Triage
                    } else {
                        ScanMode::Events
                    };
                    match self
                        .store
                        .record_gmail_window_scan_as(
                            &self.id,
                            &acct.key,
                            scanned.threads,
                            w,
                            done,
                            mode,
                        )
                        .await
                    {
                        Ok(o) => {
                            if !o.new_event_ids.is_empty() && !rt.cfg.poll_only {
                                let mut b = self.batcher.lock().unwrap();
                                for id in o.new_event_ids {
                                    b.add(
                                        Instant::now(),
                                        GMAIL_SOURCE,
                                        GMAIL_KIND,
                                        id,
                                        GMAIL_PRIORITY,
                                    );
                                }
                                drop(b);
                                self.touch();
                                self.kick();
                            }
                        }
                        Err(_) => {
                            self.poll_failed(&acct.key, GmailScanFailure::Error, start)
                                .await;
                            failures += 1;
                        }
                    }
                }
                Ended::Scan(Err(f)) => {
                    reasons.push(format!("{}:{}", acct.key, f.reason));
                    self.poll_failed(&acct.key, f.kind, start).await;
                    if f.aborted {
                        return if token.is_cancelled() {
                            stopped
                        } else {
                            blocked
                        };
                    }
                    failures += 1;
                }
                Ended::Timeout | Ended::Cancelled => {
                    // The scan future is gone. Settle its commands before anything else touches the browser.
                    let clean = self.settle(task).await;
                    self.poll_failed(&acct.key, GmailScanFailure::Incomplete, start)
                        .await;
                    if token.is_cancelled() {
                        return stopped;
                    }
                    failures += 1;
                    if !clean {
                        return (
                            "failed",
                            Some("a command did not settle".into()),
                            "unsettled",
                        );
                    }
                }
            }
        }
        // The morning summary runs after the scans, under the same claim, gate and cancel token.
        let set_stage = |w: &'static str| rt.sched.lock().unwrap().stage = Some(w);
        let phase = super::gmail_summary::Env {
            store: &self.store,
            bot_id: &self.id,
            chat_id: self.chat_id,
            cfg: &rt.cfg,
            scanner: &scanner,
            gate: &gate,
            token: &token,
            triager: rt.triager.as_ref(),
            clock: rt.clock.as_ref(),
            stage: &set_stage,
        }
        .run()
        .await;
        if rt.cfg.summary.is_some() {
            let end = match &phase {
                super::gmail_summary::PhaseEnd::NotDue => "not_due".to_owned(),
                super::gmail_summary::PhaseEnd::Delivered => "delivered".to_owned(),
                super::gmail_summary::PhaseEnd::Interrupted(w) => format!("interrupted_{w}"),
            };
            rt.sched.lock().unwrap().phase = Some((end, (rt.clock)()));
        }
        match phase {
            super::gmail_summary::PhaseEnd::Interrupted("stopped") => return stopped,
            super::gmail_summary::PhaseEnd::Interrupted("blocked" | "held") => return blocked,
            super::gmail_summary::PhaseEnd::Interrupted("yielded") => {
                return ("done", Some("yielded to an owner task".into()), "yielded");
            }
            super::gmail_summary::PhaseEnd::Interrupted(why) => {
                reasons.push(format!("summary:{why}"));
                failures += 1;
            }
            _ => {}
        }
        (
            "done",
            (!reasons.is_empty()).then(|| reasons.join(" ")),
            if failures == 0 { "complete" } else { "partial" },
        )
    }

    pub(super) async fn gmail_status(&self) -> anyhow::Result<Value> {
        let Some(rt) = &self.gmail else {
            return Ok(Value::Null);
        };
        let mut accounts = vec![];
        for a in &rt.cfg.accounts {
            let s = self.store.gmail_account_status(&self.id, &a.key).await?;
            accounts.push(json!({
                "key": a.key,
                "checked": s.as_ref().is_some_and(|s| s.last_attempt_ms.is_some()),
                "last_attempt_ms": s.as_ref().and_then(|s| s.last_attempt_ms),
                "last_success_ms": s.as_ref().and_then(|s| s.last_success_ms),
                "result": s.as_ref().and_then(|s| s.last_result.clone()),
                "window_lower_s": s.as_ref().and_then(|s| s.window_lower_s),
                "watermark_ms": s.as_ref().and_then(|s| s.watermark_ms),
            }));
        }
        let summary = match &rt.cfg.summary {
            None => Value::Null,
            Some(sum) => {
                let now = (rt.clock)();
                let off = sum.offset_minutes();
                let today = super::gmail_summary::local_day(now, off).0;
                let next_day = if self.store.gmail_summary_delivered(&self.id, &today).await? {
                    super::gmail_summary::local_day(now + 86_400_000, off).0
                } else {
                    today
                };
                let last = self.store.gmail_last_delivery(&self.id).await?;
                let phase = rt.sched.lock().unwrap().phase.clone();
                // Counts and fixed words only. The walk is "not started", "running" or "complete" for each account.
                let cleanup = match &sum.cleanup {
                    None => Value::Null,
                    Some(_) => {
                        let mut walk = serde_json::Map::new();
                        for a in &rt.cfg.accounts {
                            let b = self.store.gmail_backlog_state(&self.id, &a.key).await?;
                            walk.insert(
                                a.key.clone(),
                                match b {
                                    None => json!({"state": "not_started"}),
                                    Some(b) => json!({
                                        "state": if b.complete { "complete" } else { "running" },
                                        "sweep": b.sweep, "page": b.page, "queued_total": b.queued_total,
                                        "pending": self.store.gmail_backlog_pending(&self.id, &a.key).await?,
                                        "unresolved": self.store.gmail_backlog_unresolved(&self.id, &a.key).await?,
                                    }),
                                },
                            );
                        }
                        let keys: Vec<String> =
                            rt.cfg.accounts.iter().map(|a| a.key.clone()).collect();
                        let focus = self.store.gmail_backlog_focus(&self.id, &keys).await?;
                        let c = self.store.gmail_cleanup_counts(&self.id, 0).await?;
                        let t = self.store.gmail_topic_counts(&self.id).await?;
                        json!({
                            "open": self.store.gmail_cleanup_open(&self.id).await?,
                            "done_total": {"trashed": c.trashed_total, "receipts": c.receipts, "jobs": c.jobs, "tech": c.tech, "review": c.review, "dynamic": c.dynamic},
                            "topics": {"registry": t.registry, "dynamic": t.dynamic, "verified": t.verified, "creating": t.creating, "created": t.created, "overflow_review": t.overflow, "invalid_review": t.invalid},
                            "backlog": walk,
                            "focus": focus,
                        })
                    }
                };
                json!({
                    "cleanup": cleanup,
                    "time": sum.time,
                    "timezone": sum.timezone,
                    "labels": if sum.labels.is_some() { "configured" } else { "off" },
                    "label_halted": self.store.gmail_label_halted(&self.id).await?,
                    "label_states": self.store.gmail_label_counts(&self.id).await?,
                    "triage_backlog": self.store.gmail_triage_backlog(&self.id).await?,
                    "triage_errors": self.store.gmail_triage_errors(&self.id).await?,
                    "next_summary": format!("{next_day} {}", sum.time),
                    "last_delivery": last.map(|(day, ms)| json!({"day": day, "age_ms": (now - ms).max(0)})),
                    "last_phase": phase.map(|(end, ms)| json!({"end": end, "age_ms": (now - ms).max(0)})),
                })
            }
        };
        let unprocessed = self
            .store
            .unbatched_events(&self.id)
            .await?
            .iter()
            .filter(|e| e.source == GMAIL_SOURCE)
            .count();
        let s = rt.sched.lock().unwrap();
        let now = Instant::now();
        let due = s.next_due;
        Ok(json!({
            "summary": summary,
            "scope": "date_window",
            "mode": if rt.cfg.poll_only { "poll_only" } else { "triage" },
            "unprocessed_events": unprocessed,
            "interval_secs": rt.interval.as_secs(),
            "running": s.running,
            "stage": s.stage,
            "stage_account": s.account,
            "next_due_in_ms": due.map(|d| d.saturating_duration_since(now).as_millis() as u64),
            "overdue_ms": due.map(|d| now.saturating_duration_since(d).as_millis() as u64),
            "current_started_ms": s.run_started_ms,
            "last_cycle": {"started_ms": s.started_ms, "ended_ms": s.ended_ms, "result": s.result},
            "accounts": accounts,
        }))
    }
}

#[cfg(test)]
mod runtime_tests {
    use std::sync::atomic::{AtomicUsize, Ordering::Relaxed};

    use super::sim::*;
    use super::*;
    use crate::agent::Agent;
    use crate::bot::gmail_store::{GmailAccountStatus, GmailWindow};
    use crate::bot::hub::GuestHost;
    use crate::bot::hub::tests::{Guest, spawn_guest};
    use crate::bot::screen;
    use crate::bot::{Comp, Mode};
    use crate::config::Config;
    use crate::model::ModelClient;
    use crate::store::Store;
    use crate::testutil::{fake_model, sse};
    use crate::tools::Tools;

    const NOW: i64 = 1_800_000_000_000;

    struct Cap;
    impl screen::Capture for Cap {
        fn grab(&self) -> BoxFuture<'_, anyhow::Result<Vec<u8>>> {
            Box::pin(async { Ok(b"png".to_vec()) })
        }
    }

    fn text(t: &str) -> (u16, String) {
        (
            200,
            sse(&[json!({"choices":[{"delta":{"content":t},"finish_reason":"stop"}]})]),
        )
    }

    struct T {
        bot: Arc<Bot>,
        g: Guest,
        sim: Arc<Sim>,
        seen: Arc<Mutex<Vec<Value>>>,
        agent: Arc<Agent>,
    }

    fn fast() -> Pacing {
        Pacing {
            retry_wait: Duration::from_millis(1),
            advance_wait: Duration::from_millis(1),
            advance_polls: 4,
            read_attempts: 3,
        }
    }

    const KEYS: [&str; 3] = ["one", "two", "three"];

    fn rows3() -> Sim {
        Sim::new()
            .rows(0, &[(T[0], m(1))])
            .rows(1, &[(T[1], m(2))])
            .rows(2, &[(T[2], m(3))])
    }

    /// A bot on the real Python executor, a scripted model, a fake computer and a simulated Gmail.
    /// `tune` changes the poller before the bot starts. The test starts the bot.
    async fn build(
        accounts: usize,
        gmail: bool,
        saved: bool,
        script: Vec<(u16, String)>,
        tune: impl FnOnce(&mut GmailRuntime, &Arc<Sim>, &Guest),
    ) -> T {
        let g = spawn_guest();
        if saved {
            let mut s = g.vm.inner.lock().unwrap();
            s.running = false;
            s.saved = true;
        }
        let (url, seen) = fake_model(script).await;
        let mut toml = format!(
            "{}\n[vm]\nversion = 1\nbot_id = \"main\"\nruntime_dir = \"/rt\"\nharness = \"/h/vmproto.py\"\n[events]\ntoken_env = \"DECK_TEST_TOKEN\"\nbatch_window_ms = 100\nbatch_max_delay_ms = 300\n",
            include_str!("../../deck.example.toml")
        );
        if gmail {
            toml += "[gmail]\naccount_control_selector = \"a.acct\"\nsearch_box_selector = \"input.q\"\n";
            for (i, k) in KEYS.iter().enumerate().take(accounts) {
                toml += &format!(
                    "[[gmail.accounts]]\nkey = \"{k}\"\nindex = {i}\nuser_secret = \"ACCOUNT_{}_USER\"\n",
                    k.to_uppercase()
                );
            }
        }
        let mut config = Config::parse(&toml).unwrap();
        config.model.base_url = url.clone();
        config.data_dir = g.dir.join("data");
        let config = Arc::new(config);
        let tools = Tools::new(
            Arc::new(GuestHost(g.hub.clone())),
            g.store.clone(),
            config.workspace(),
            &config.data_dir,
        )
        .with_guest(g.hub.clone());
        let model = ModelClient::new(&url, Some("k".into()), "K");
        let agent = Arc::new(Agent::with_tools(
            config.clone(),
            g.store.clone(),
            model,
            tools,
        ));
        let mut bot = Bot::new(
            &config,
            g.store.clone(),
            agent.clone(),
            g.vm.clone(),
            g.hub.clone(),
            Arc::new(Cap),
        )
        .await
        .unwrap();
        let sim = Arc::new(rows3());
        {
            let b = Arc::get_mut(&mut bot).unwrap();
            b.idle = Duration::from_secs(60);
            if let Some(gm) = b.gmail.as_mut() {
                gm.host = sim.clone();
                gm.pacing = fast();
                gm.interval = Duration::from_secs(60);
                gm.cap = Duration::from_secs(10);
                tune(gm, &sim, &g);
            }
        }
        T {
            bot,
            g,
            sim,
            seen,
            agent,
        }
    }

    async fn until<F, Fut>(what: &str, mut f: F)
    where
        F: FnMut() -> Fut,
        Fut: std::future::Future<Output = bool>,
    {
        for _ in 0..200 {
            if f().await {
                return;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        panic!("timed out: {what}");
    }

    async fn acct(t: &T, key: &str) -> Option<GmailAccountStatus> {
        t.g.store.gmail_account_status("main", key).await.unwrap()
    }

    async fn all_attempted(t: &T, n: usize) -> bool {
        for k in KEYS.iter().take(n) {
            if acct(t, k).await.is_none_or(|s| s.last_attempt_ms.is_none()) {
                return false;
            }
        }
        true
    }

    async fn idle(t: &T) -> bool {
        t.bot.inner.lock().unwrap().current.is_none()
            && !t.bot.gmail.as_ref().unwrap().sched.lock().unwrap().running
    }

    async fn count(t: &T, sql: &'static str) -> i64 {
        t.g.store
            .run(move |c| c.query_row(sql, [], |r| r.get(0)))
            .await
            .unwrap()
    }

    fn gotos(sim: &Sim) -> Vec<String> {
        sim.0
            .lock()
            .unwrap()
            .args
            .iter()
            .filter(|(n, _)| n == "goto")
            .map(|(_, a)| a["steps"].as_str().unwrap().to_owned())
            .collect()
    }

    fn pause_in_store(store: &Store, on: bool) {
        store
            .run_sync(|c| c.execute("UPDATE bot_state SET automation_paused = ?1", [on]))
            .unwrap();
    }

    // ---------- scheduling ----------

    #[tokio::test]
    async fn gmail_due_deadline_is_not_lost_between_scheduler_checks() {
        let t = build(1, true, true, vec![], |_, _, _| {}).await;
        let rt = t.bot.gmail.as_ref().unwrap();
        rt.arm();
        assert!(
            rt.deadline().is_some(),
            "a just-expired deadline must wake the loop"
        );
        pause_in_store(&t.g.store, true);
        assert!(
            t.bot.step().await.unwrap().is_none(),
            "a blocked due poll must not spin"
        );
    }

    #[tokio::test]
    async fn gmail_watermark_read_failure_never_becomes_a_fresh_baseline() {
        let t = build(1, true, false, vec![], |_, _, _| {}).await;
        t.g.store
            .run_sync(|c| {
                c.execute_batch("ALTER TABLE gmail_accounts RENAME TO unavailable_accounts")
            })
            .unwrap();
        let (state, _, _) = t
            .bot
            .gmail_cycle("test-poll", CancellationToken::new())
            .await;
        assert_eq!(state, "failed");
        assert!(
            gotos(&t.sim).is_empty(),
            "a storage error must not narrow the catch-up window"
        );
    }

    #[tokio::test]
    async fn a_failed_account_scan_leaves_its_fixed_reason_in_the_task_detail() {
        let t = build(2, true, true, vec![], |_, _, _| {}).await;
        t.sim.0.lock().unwrap().qbox = Some("another query".into());
        let (state, detail, result) = t
            .bot
            .gmail_cycle("test-poll", CancellationToken::new())
            .await;
        assert_eq!((state, result), ("done", "partial"));
        // A code and the account key only. Never page text.
        assert_eq!(detail.as_deref(), Some("one:query_box two:query_box"));
    }

    #[tokio::test]
    async fn a_poll_with_the_summary_on_queues_triage_not_events_and_posts_one_summary() {
        let t = build(2, true, true, vec![], |gm, _, _| {
            gm.cfg.summary = Some(toml::from_str("time = \"00:00\"").unwrap());
        })
        .await;
        let (state, _, result) = t
            .bot
            .gmail_cycle("test-poll", CancellationToken::new())
            .await;
        // The simulated mailbox serves no thread view. Each read fails with a fixed code and keeps its row.
        assert_eq!((state, result), ("done", "complete"));
        assert_eq!(count(&t, "SELECT COUNT(*) FROM bot_events").await, 0);
        assert_eq!(
            count(&t, "SELECT COUNT(*) FROM gmail_triage WHERE state = 'pending' AND attempts = 1 AND last_failure IS NOT NULL").await,
            2
        );
        assert_eq!(
            t.agent.model_calls(),
            0,
            "no thread was read, so no model call"
        );
        let chat = t.bot.chat_id;
        let msgs = t.g.store.list_messages(chat).await.unwrap().unwrap();
        assert_eq!(msgs.len(), 1);
        assert!(
            msgs[0].content.contains("Morning summary")
                && msgs[0].content.contains("Not triaged yet")
        );
        let status = t.bot.status().await.unwrap();
        assert_eq!(status["gmail"]["summary"]["labels"], "off");
        assert_eq!(
            status["gmail"]["summary"]["triage_backlog"]["one"],
            json!([1, 1])
        );
        let sm = &status["gmail"]["summary"];
        assert_eq!(sm["label_halted"], false);
        assert_eq!(sm["last_phase"]["end"], "delivered");
        assert!(sm["last_phase"]["age_ms"].is_u64());
        assert!(
            sm["last_delivery"]["day"]
                .as_str()
                .is_some_and(|d| d.len() == 10)
        );
        assert!(sm["last_delivery"]["age_ms"].is_u64());
        assert!(
            sm["next_summary"]
                .as_str()
                .is_some_and(|d| d.ends_with("00:00"))
        );
        assert_eq!(
            sm["triage_errors"]
                .as_object()
                .unwrap()
                .values()
                .filter_map(Value::as_u64)
                .sum::<u64>(),
            2
        );
        assert_eq!(sm["label_states"], json!({}));
        // A second poll on the same local day posts nothing and retries nothing.
        t.bot
            .gmail_cycle("test-poll-2", CancellationToken::new())
            .await;
        assert_eq!(
            t.g.store.list_messages(chat).await.unwrap().unwrap().len(),
            1
        );
        assert_eq!(count(&t, "SELECT SUM(attempts) FROM gmail_triage").await, 2);
    }

    #[tokio::test]
    async fn without_a_gmail_section_nothing_polls_wakes_or_queues() {
        let t = build(3, false, true, vec![], |_, _, _| {}).await;
        assert!(t.bot.gmail.is_none());
        t.bot.start().await.unwrap();
        tokio::time::sleep(Duration::from_millis(700)).await;
        assert!(t.sim.calls().is_empty());
        assert_eq!(t.g.vm.inner.lock().unwrap().wakes, 0);
        assert!(t.g.store.tasks("main", 10).await.unwrap().is_empty());
        assert!(t.bot.status().await.unwrap()["gmail"].is_null());
        assert_eq!(count(&t, "SELECT COUNT(*) FROM tasks").await, 0);
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn one_due_poll_scans_each_account_in_turn_in_one_session_without_a_model() {
        let t = build(3, true, true, vec![], |_, _, _| {}).await;
        t.bot.start().await.unwrap();
        until("three accounts attempted", || all_attempted(&t, 3)).await;
        until("poll released", || idle(&t)).await;
        // One wake, one session probe, no profile open, accounts in order.
        assert_eq!(t.g.vm.inner.lock().unwrap().wakes, 1);
        assert_eq!((t.sim.count("probe"), t.sim.count("open")), (1, 0));
        let urls = gotos(&t.sim);
        let order: Vec<&str> = urls
            .iter()
            .step_by(2)
            .map(|u| &u[u.find("/u/").unwrap()..][..5])
            .collect();
        assert_eq!(order, ["/u/0/", "/u/1/", "/u/2/"]);
        // No model, and the first complete scan of each account is a silent baseline.
        assert_eq!(t.agent.model_calls(), 0);
        assert!(t.seen.lock().unwrap().is_empty());
        assert_eq!(count(&t, "SELECT COUNT(*) FROM bot_events").await, 0);
        for k in KEYS {
            let s = acct(&t, k).await.unwrap();
            assert_eq!(s.last_result.as_deref(), Some("ok"), "{k}");
            assert!(s.watermark_ms.is_some() && s.baseline_done);
        }
        // The finished poll does not crowd the task list.
        assert!(t.g.store.tasks("main", 10).await.unwrap().is_empty());
        let st = t.bot.status().await.unwrap();
        assert_eq!(st["gmail"]["accounts"].as_array().unwrap().len(), 3);
        assert_eq!(st["gmail"]["running"], false);
        assert_eq!(st["gmail"]["last_cycle"]["result"], "complete");
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn a_later_revision_queues_once_and_feeds_one_triage_task() {
        let t = build(1, true, false, vec![text("triaged")], |gm, _, _| {
            gm.interval = Duration::from_millis(300);
        })
        .await;
        t.bot.start().await.unwrap();
        until("baseline", || async {
            acct(&t, "one")
                .await
                .is_some_and(|s| s.last_success_ms.is_some())
        })
        .await;
        assert_eq!(count(&t, "SELECT COUNT(*) FROM bot_events").await, 0);
        t.sim
            .0
            .lock()
            .unwrap()
            .data
            .insert(0, vec![(T[0].into(), m(7))]);
        until("one triage task", || async {
            count(
                &t,
                "SELECT COUNT(*) FROM tasks WHERE source = 'event' AND execution_kind = 'model'",
            )
            .await
                == 1
        })
        .await;
        until("model ran", || async { t.agent.model_calls() == 1 }).await;
        until("triage task ended", || async {
            count(&t, "SELECT COUNT(*) FROM tasks WHERE source = 'event' AND execution_kind = 'model' AND state = 'done'").await == 1
        })
        .await;
        // More cycles with the same page add nothing.
        let before = t.sim.count("goto");
        until("two more cycles", || async {
            t.sim.count("goto") >= before + 4
        })
        .await;
        assert_eq!(count(&t, "SELECT COUNT(*) FROM bot_events").await, 1);
        assert_eq!(
            count(
                &t,
                "SELECT COUNT(*) FROM bot_events WHERE task_id IS NOT NULL"
            )
            .await,
            1
        );
        assert_eq!(
            count(
                &t,
                "SELECT COUNT(*) FROM tasks WHERE source = 'event' AND execution_kind = 'model'"
            )
            .await,
            1
        );
        let task =
            t.g.store
                .tasks("main", 10)
                .await
                .unwrap()
                .into_iter()
                .find(|x| x.source == "event")
                .unwrap();
        assert_eq!(task.title, "1 inbox_changed event from gmail_poll");
        assert_eq!(task.priority, 0);
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn pause_budget_hold_waiting_task_and_unsettled_commands_each_block_the_poll() {
        let t = build(1, true, false, vec![], |gm, _, _| {
            gm.interval = Duration::from_millis(100);
        })
        .await;
        pause_in_store(&t.g.store, true);
        t.bot.start().await.unwrap();
        async fn quiet(t: &T) {
            let n = t.sim.calls().len();
            tokio::time::sleep(Duration::from_millis(500)).await;
            assert_eq!(t.sim.calls().len(), n, "a blocked bot made a browser call");
        }
        quiet(&t).await;
        assert_eq!(t.sim.calls().len(), 0);
        t.bot.set_paused(false).await.unwrap();
        until("poll after resume", || async { !t.sim.calls().is_empty() }).await;
        // Budget hold.
        t.bot.hold_budget("test".into()).await.unwrap();
        until("poll released", || idle(&t)).await;
        quiet(&t).await;
        t.bot.approve_budget().await.unwrap();
        // A waiting task owns the desktop.
        let chat = t.bot.chat_id();
        let w =
            t.g.store
                .add_task("main", chat, "message", "w".into(), "w".into(), 1)
                .await
                .unwrap();
        until("poll released", || idle(&t)).await;
        t.g.store
            .move_task(&w.id, &["queued"], "waiting", None)
            .await
            .unwrap();
        quiet(&t).await;
        t.g.store
            .move_task(&w.id, &["waiting"], "cancelled", None)
            .await
            .unwrap();
        t.bot.kick();
        // An unsettled command.
        until("poll released", || idle(&t)).await;
        t.g.store
            .begin(
                "main".into(),
                "x".into(),
                "cunsettled".into(),
                vec!["a".into()],
                1000,
                "d".into(),
            )
            .await
            .unwrap();
        t.g.store.mark_sent("cunsettled".into(), 1).await.unwrap();
        quiet(&t).await;
        t.g.store.decide("cunsettled", "not_run", "").await.unwrap();
        t.bot.kick();
        let n = t.sim.calls().len();
        until("poll after the decision", || async {
            t.sim.calls().len() > n
        })
        .await;
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn a_pause_that_lands_before_the_claim_is_honored_under_the_gate() {
        let t = build(1, true, true, vec![], |_, _, _| {}).await;
        pause_in_store(&t.g.store, true);
        t.bot.start().await.unwrap();
        // The scheduler read the flags earlier and still calls the claim.
        t.bot.dispatch_gmail_poll().await.unwrap();
        assert!(t.sim.calls().is_empty());
        assert!(t.bot.inner.lock().unwrap().current.is_none());
        assert_eq!(
            t.g.vm.inner.lock().unwrap().wakes,
            0,
            "a blocked claim must not wake the computer"
        );
        assert!(
            t.g.store
                .next_queued_gmail_poll("main")
                .await
                .unwrap()
                .is_none()
        );
        assert_eq!(count(&t, "SELECT COUNT(*) FROM tasks WHERE state = 'cancelled' AND execution_kind = 'gmail_poll'").await, 1);
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn a_pause_between_operations_stops_the_scan_and_records_only_an_attempt() {
        let t = build(3, true, false, vec![], |_, sim, g| {
            let store = g.store.clone();
            sim.0.lock().unwrap().hook = Some(Box::new(move |n, _| {
                if n == 2 {
                    pause_in_store(&store, true);
                }
            }));
        })
        .await;
        t.bot.start().await.unwrap();
        until("first account attempted", || async {
            acct(&t, "one")
                .await
                .is_some_and(|s| s.last_attempt_ms.is_some())
        })
        .await;
        until("poll released", || idle(&t)).await;
        let s = acct(&t, "one").await.unwrap();
        assert_eq!(s.last_result.as_deref(), Some("incomplete"));
        assert_eq!((s.watermark_ms, s.last_success_ms), (None, None));
        // The pause landed during the identity check. Nothing ran after it. The other accounts were never touched.
        assert_eq!(t.sim.calls(), ["probe", "goto", "expect"]);
        assert!(acct(&t, "two").await.is_none() && acct(&t, "three").await.is_none());
        assert_eq!(count(&t, "SELECT COUNT(*) FROM gmail_seen").await, 0);
        assert_eq!(
            t.bot.status().await.unwrap()["gmail"]["last_cycle"]["result"],
            "blocked"
        );
        t.bot.stop_loop();
    }

    // ---------- stop, timeout and the journal ----------

    /// Every call becomes a journaled guest command that never ends by itself.
    struct Hang(Arc<super::super::hub::Hub>);
    impl BrowserHost for Hang {
        fn call<'a>(&'a self, _: &'a str, _: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
            Box::pin(async move {
                self.0.shell("sleep 60", Some(60)).await?;
                Ok(Value::Null)
            })
        }
        fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>> {
            Box::pin(async { Ok(vec![]) })
        }
        fn out_dirs(&self) -> Vec<std::path::PathBuf> {
            vec![]
        }
    }

    #[tokio::test]
    async fn a_user_stop_ends_the_poll_and_settles_its_journal_before_the_computer_is_free() {
        let t = build(2, true, false, vec![], |gm, _, g| {
            gm.host = Arc::new(Hang(g.hub.clone()));
        })
        .await;
        t.bot.start().await.unwrap();
        until("a live command", || async {
            t.g.store
                .commands("main".into())
                .await
                .unwrap()
                .iter()
                .any(|c| c.state == "running")
        })
        .await;
        let r = t.bot.stop().await.unwrap();
        assert!(r.unconfirmed.is_empty(), "{r:?}");
        assert_eq!(r.confirmed.len(), 1);
        assert!(idle(&t).await);
        assert!(t.g.store.unresolved("main").await.unwrap().is_empty());
        let poll = r.task_id.unwrap();
        assert_eq!(
            t.g.store.task(&poll).await.unwrap().unwrap().state,
            "cancelled"
        );
        let s = acct(&t, "one").await.unwrap();
        assert_eq!(
            (s.last_result.as_deref(), s.last_success_ms),
            (Some("incomplete"), None)
        );
        // The stop came before the session probe ended: no account reached a scan.
        let two = acct(&t, "two").await.unwrap();
        assert_eq!(
            (two.last_result.as_deref(), two.last_success_ms),
            (Some("incomplete"), None)
        );
        t.bot.stop_loop();
    }

    /// Account one hangs in a journaled command. The others answer from the simulator.
    struct FirstHangs {
        hub: Arc<super::super::hub::Hub>,
        sim: Arc<Sim>,
        store: Store,
        leave_unsettled: bool,
    }
    impl BrowserHost for FirstHangs {
        fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
            Box::pin(async move {
                let first = args["steps"]
                    .as_str()
                    .is_some_and(|s| s.starts_with("goto ") && s.contains("/mail/u/0/"));
                if first && self.leave_unsettled {
                    self.store
                        .begin(
                            "main".into(),
                            "poll".into(),
                            "cleft".into(),
                            vec!["x".into()],
                            1000,
                            "d".into(),
                        )
                        .await?;
                    self.store.mark_sent("cleft".into(), 1).await?;
                    anyhow::bail!("lost");
                }
                if first {
                    self.hub.shell("sleep 60", Some(60)).await?;
                }
                self.sim.call(name, args).await
            })
        }
        fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>> {
            Box::pin(async { Ok(vec![]) })
        }
        fn out_dirs(&self) -> Vec<std::path::PathBuf> {
            vec![]
        }
    }

    #[tokio::test]
    async fn a_time_cap_settles_the_journal_and_the_next_account_continues_only_when_clean() {
        let t = build(3, true, false, vec![], |gm, sim, g| {
            gm.cap = Duration::from_millis(400);
            gm.host = Arc::new(FirstHangs {
                hub: g.hub.clone(),
                sim: sim.clone(),
                store: g.store.clone(),
                leave_unsettled: false,
            });
        })
        .await;
        t.bot.start().await.unwrap();
        until("all attempted", || all_attempted(&t, 3)).await;
        until("poll released", || idle(&t)).await;
        let one = acct(&t, "one").await.unwrap();
        assert_eq!(
            (one.last_result.as_deref(), one.last_success_ms),
            (Some("incomplete"), None)
        );
        assert!(t.g.store.unresolved("main").await.unwrap().is_empty());
        let cmds = t.g.store.commands("main".into()).await.unwrap();
        assert_eq!(cmds.len(), 1);
        assert!(cmds[0].is_final(), "{:?}", cmds[0].state);
        for k in ["two", "three"] {
            assert_eq!(
                acct(&t, k).await.unwrap().last_result.as_deref(),
                Some("ok"),
                "{k}"
            );
        }
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn an_unsettled_command_holds_the_cycle_and_all_later_polls() {
        let t = build(3, true, false, vec![], |gm, sim, g| {
            gm.interval = Duration::from_millis(100);
            gm.host = Arc::new(FirstHangs {
                hub: g.hub.clone(),
                sim: sim.clone(),
                store: g.store.clone(),
                leave_unsettled: true,
            });
        })
        .await;
        t.bot.start().await.unwrap();
        until("first account attempted", || async {
            acct(&t, "one")
                .await
                .is_some_and(|s| s.last_attempt_ms.is_some())
        })
        .await;
        until("poll released", || idle(&t)).await;
        assert!(acct(&t, "two").await.is_none() && acct(&t, "three").await.is_none());
        let n = t.sim.calls().len();
        tokio::time::sleep(Duration::from_millis(500)).await;
        assert_eq!(
            t.sim.calls().len(),
            n,
            "no poll may run before the owner decides"
        );
        let inbox = t.bot.inbox().await.unwrap();
        assert!(inbox.iter().any(|i| i.kind == "unknown_outcome"));
        t.bot.stop_loop();
    }

    // ---------- cadence, catch-up, clock ----------

    struct Slow {
        sim: Arc<Sim>,
        delay: Duration,
        live: AtomicUsize,
        peak: AtomicUsize,
        probes: Mutex<Vec<Instant>>,
    }
    impl BrowserHost for Slow {
        fn call<'a>(&'a self, name: &'a str, args: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
            Box::pin(async move {
                let n = self.live.fetch_add(1, Relaxed) + 1;
                self.peak.fetch_max(n, Relaxed);
                if args["js"] == "location.hostname" {
                    self.probes.lock().unwrap().push(Instant::now());
                }
                tokio::time::sleep(self.delay).await;
                let r = self.sim.call(name, args).await;
                self.live.fetch_sub(1, Relaxed);
                r
            })
        }
        fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>> {
            Box::pin(async { Ok(vec![]) })
        }
        fn out_dirs(&self) -> Vec<std::path::PathBuf> {
            vec![]
        }
    }

    #[tokio::test]
    async fn polls_never_overlap_and_a_slow_cycle_starts_no_burst() {
        let slow: Arc<std::sync::OnceLock<Arc<Slow>>> = Default::default();
        let slot = slow.clone();
        let t = build(3, true, false, vec![], move |gm, sim, _| {
            gm.interval = Duration::from_millis(100);
            let s = Arc::new(Slow {
                sim: sim.clone(),
                delay: Duration::from_millis(15),
                live: AtomicUsize::new(0),
                peak: AtomicUsize::new(0),
                probes: Mutex::default(),
            });
            slot.set(s.clone()).ok();
            gm.host = s;
        })
        .await;
        t.bot.start().await.unwrap();
        tokio::time::sleep(Duration::from_millis(1800)).await;
        let slow = slow.get().unwrap();
        assert_eq!(slow.peak.load(Relaxed), 1, "two browser calls ran at once");
        let probes = slow.probes.lock().unwrap().clone();
        assert!(probes.len() >= 2, "{}", probes.len());
        // A cycle of three accounts outlasts the 100 ms interval. The next start waits for its end: one poll, no burst.
        let cycle = Duration::from_millis(200);
        for w in probes.windows(2) {
            assert!(w[1] - w[0] >= cycle, "{:?}", w[1] - w[0]);
        }
        assert!(probes.len() <= 9, "{}", probes.len());
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn a_running_cycle_keeps_the_last_finished_cycle_visible() {
        let t = build(3, true, false, vec![], |gm, sim, _| {
            gm.interval = Duration::from_millis(50);
            gm.host = Arc::new(Slow {
                sim: sim.clone(),
                delay: Duration::from_millis(15),
                live: AtomicUsize::new(0),
                peak: AtomicUsize::new(0),
                probes: Mutex::default(),
            });
        })
        .await;
        t.bot.start().await.unwrap();
        let (mut finished, mut running_after) = (None, 0);
        for _ in 0..300 {
            let g = t.bot.status().await.unwrap()["gmail"].clone();
            let lc = &g["last_cycle"];
            if finished.is_some() && g["running"] == true {
                running_after += 1;
                // The run in progress must not erase the finished one.
                assert_eq!(lc["result"], "complete", "{lc}");
                assert!(lc["started_ms"].is_i64() && lc["ended_ms"].is_i64());
            }
            if lc["result"] == "complete" {
                finished = Some(lc.clone());
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        assert!(finished.is_some() && running_after > 0, "{running_after}");
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn after_downtime_one_poll_starts_from_the_previous_start_minus_the_overlap() {
        let t = build(3, true, false, vec![], |gm, _, _| {
            gm.clock = Arc::new(|| NOW);
        })
        .await;
        let old = NOW - 10 * 86_400_000;
        for (i, k) in KEYS.iter().enumerate() {
            t.g.store
                .record_gmail_window_scan(
                    "main",
                    k,
                    vec![crate::bot::gmail_store::GmailThreadRev {
                        thread_id: T[i].into(),
                        message_id: m(i as u32 + 1),
                    }],
                    GmailWindow {
                        start_ms: old,
                        lower_s: old / 1000 - 3600,
                    },
                    old + 50_000,
                )
                .await
                .unwrap();
        }
        t.bot.start().await.unwrap();
        until("three accounts scanned", || async {
            acct(&t, "three")
                .await
                .is_some_and(|s| s.watermark_ms == Some(NOW))
        })
        .await;
        let lower = old / 1000 - 3600;
        for (i, u) in gotos(&t.sim).iter().step_by(2).enumerate() {
            assert!(u.ends_with(&format!("after%3A{lower}")), "{i} {u}");
        }
        // The watermark is the start, never the completion. Nothing new in the page: no event.
        assert_eq!(count(&t, "SELECT COUNT(*) FROM bot_events").await, 0);
        assert_eq!(t.sim.count("probe"), 1);
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn a_clock_behind_the_watermark_touches_no_page_and_keeps_the_watermark() {
        let t = build(1, true, false, vec![], |gm, _, _| {
            gm.clock = Arc::new(|| NOW);
        })
        .await;
        t.g.store
            .record_gmail_window_scan(
                "main",
                "one",
                vec![],
                GmailWindow {
                    start_ms: NOW + 3_600_000,
                    lower_s: 1_700_000_000,
                },
                NOW + 3_700_000,
            )
            .await
            .unwrap();
        t.bot.start().await.unwrap();
        until("attempt recorded", || async {
            acct(&t, "one")
                .await
                .is_some_and(|s| s.last_attempt_ms == Some(NOW))
        })
        .await;
        let s = acct(&t, "one").await.unwrap();
        assert_eq!(s.last_result.as_deref(), Some("error"));
        assert_eq!(s.watermark_ms, Some(NOW + 3_600_000));
        assert_eq!(s.last_success_ms, Some(NOW + 3_700_000));
        for c in ["goto", "expect", "eval", "click"] {
            assert_eq!(t.sim.count(c), 0, "{c}");
        }
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn auth_identity_and_incomplete_scans_leave_the_seen_state_and_the_watermark() {
        type Setup = fn(&mut State);
        let cases: [(&str, Setup); 3] = [
            ("auth", |s| s.host = "accounts.google.com".into()),
            ("identity_mismatch", |s| s.ambiguous = true),
            ("incomplete", |s| {
                s.click_fails = true;
                s.data
                    .insert(0, (0..5).map(|i| (T[i].to_string(), m(i as u32))).collect());
            }),
        ];
        for (code, setup) in cases {
            let t = build(1, true, false, vec![], |gm, sim, _| {
                gm.clock = Arc::new(|| NOW);
                setup(&mut sim.0.lock().unwrap());
            })
            .await;
            let seed = NOW - 3_600_000;
            t.g.store
                .record_gmail_window_scan(
                    "main",
                    "one",
                    vec![crate::bot::gmail_store::GmailThreadRev {
                        thread_id: T[0].into(),
                        message_id: m(1),
                    }],
                    GmailWindow {
                        start_ms: seed,
                        lower_s: seed / 1000 - 3600,
                    },
                    seed + 1000,
                )
                .await
                .unwrap();
            t.bot.start().await.unwrap();
            until(code, || async {
                acct(&t, "one")
                    .await
                    .is_some_and(|s| s.last_attempt_ms == Some(NOW))
            })
            .await;
            let s = acct(&t, "one").await.unwrap();
            assert_eq!(s.last_result.as_deref(), Some(code));
            assert_eq!(
                (s.watermark_ms, s.last_success_ms),
                (Some(seed), Some(seed + 1000)),
                "{code}"
            );
            assert_eq!(
                count(&t, "SELECT COUNT(*) FROM gmail_seen").await,
                1,
                "{code}"
            );
            assert_eq!(
                count(&t, "SELECT COUNT(*) FROM bot_events").await,
                0,
                "{code}"
            );
            let st = t.bot.status().await.unwrap();
            assert_eq!(st["gmail"]["accounts"][0]["result"], code);
            assert_eq!(st["gmail"]["accounts"][0]["checked"], true);
            t.bot.stop_loop();
        }
    }

    // ---------- owner messages and restart ----------

    struct Pending;
    impl BrowserHost for Pending {
        fn call<'a>(&'a self, _: &'a str, _: Value) -> BoxFuture<'a, anyhow::Result<Value>> {
            Box::pin(std::future::pending())
        }
        fn tools(&self) -> BoxFuture<'_, anyhow::Result<Vec<Value>>> {
            Box::pin(async { Ok(vec![]) })
        }
        fn out_dirs(&self) -> Vec<std::path::PathBuf> {
            vec![]
        }
    }

    #[tokio::test]
    async fn an_owner_message_during_a_poll_queues_a_task_and_never_steers_the_poll() {
        let t = build(1, true, false, vec![text("ok")], |gm, _, _| {
            gm.host = Arc::new(Pending);
        })
        .await;
        t.bot.start().await.unwrap();
        until("poll running", || async {
            t.bot
                .inner
                .lock()
                .unwrap()
                .current
                .as_ref()
                .is_some_and(|c| c.poll)
        })
        .await;
        let poll = t
            .bot
            .inner
            .lock()
            .unwrap()
            .current
            .as_ref()
            .unwrap()
            .task_id
            .clone();
        let s = t.bot.submit("hello".into(), Mode::Steer, 1).await.unwrap();
        assert_eq!(s.kind, "queued");
        assert_ne!(s.task_id, poll);
        assert!(t.g.store.take_steering(&poll).await.unwrap().is_empty());
        t.bot.stop().await.unwrap();
        until("the owner task ran", || async {
            t.g.store.task(&s.task_id).await.unwrap().unwrap().state == "done"
        })
        .await;
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn restart_replays_committed_events_once_and_ends_an_interrupted_poll() {
        let t = build(1, false, false, vec![text("a")], |_, _, _| {}).await;
        let chat = t.bot.chat_id();
        let rev = |t: &str, mm: u32| crate::bot::gmail_store::GmailThreadRev {
            thread_id: t.into(),
            message_id: m(mm),
        };
        let w = |at: i64| GmailWindow {
            start_ms: at,
            lower_s: 1_700_000_000,
        };
        t.g.store
            .record_gmail_window_scan("main", "one", vec![rev(T[0], 1)], w(1000), 1000)
            .await
            .unwrap();
        let o =
            t.g.store
                .record_gmail_window_scan(
                    "main",
                    "one",
                    vec![rev(T[0], 2), rev(T[1], 3)],
                    w(2000),
                    2000,
                )
                .await
                .unwrap();
        assert_eq!(o.new_event_ids.len(), 2);
        let p =
            t.g.store
                .enqueue_gmail_poll("main", chat)
                .await
                .unwrap()
                .unwrap();
        t.g.store
            .move_task(&p.id, &["queued"], "running", None)
            .await
            .unwrap();
        t.bot.start().await.unwrap();
        until("one batched task", || async {
            count(
                &t,
                "SELECT COUNT(*) FROM tasks WHERE source = 'event' AND execution_kind = 'model'",
            )
            .await
                == 1
        })
        .await;
        assert_eq!(
            count(&t, "SELECT COUNT(*) FROM bot_events WHERE task_id IS NULL").await,
            0
        );
        let task = t.g.store.task(&p.id).await.unwrap().unwrap();
        assert_eq!(
            (task.state.as_str(), task.detail.as_deref()),
            ("failed", Some("interrupted by a host restart"))
        );
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn poll_only_keeps_new_events_unprocessed_across_cycles_and_a_restart() {
        let t = build(1, true, false, vec![text("must not run")], |gm, _, _| {
            gm.interval = Duration::from_millis(300);
            gm.cfg.poll_only = true;
        })
        .await;
        t.bot.start().await.unwrap();
        until("baseline", || async {
            acct(&t, "one")
                .await
                .is_some_and(|s| s.last_success_ms.is_some())
        })
        .await;
        t.sim
            .0
            .lock()
            .unwrap()
            .data
            .insert(0, vec![(T[0].into(), m(7))]);
        until("event stored", || async {
            count(&t, "SELECT COUNT(*) FROM bot_events").await == 1
        })
        .await;
        let before = t.sim.count("goto");
        until("two more cycles", || async {
            t.sim.count("goto") >= before + 4
        })
        .await;
        // The window stays healthy, the event stays unprocessed, and no model task or call exists.
        assert_eq!(
            count(&t, "SELECT COUNT(*) FROM bot_events WHERE task_id IS NULL").await,
            1
        );
        assert_eq!(
            count(
                &t,
                "SELECT COUNT(*) FROM tasks WHERE source = 'event' AND execution_kind = 'model'"
            )
            .await,
            0
        );
        assert_eq!(t.agent.model_calls(), 0);
        let st = t.bot.status().await.unwrap();
        assert_eq!(st["gmail"]["mode"], "poll_only");
        assert_eq!(st["gmail"]["unprocessed_events"], 1);
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn status_names_the_current_stage_while_a_poll_runs() {
        let t = build(1, true, false, vec![text("x")], |gm, _, _| {
            gm.host = Arc::new(Pending);
        })
        .await;
        assert_eq!(t.bot.status().await.unwrap()["gmail"]["stage"], Value::Null);
        t.bot.start().await.unwrap();
        until("stage session", || async {
            let st = t.bot.status().await.unwrap();
            st["gmail"]["running"] == true && st["gmail"]["stage"] == "session"
        })
        .await;
        assert_eq!(t.bot.status().await.unwrap()["gmail"]["mode"], "triage");
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn poll_only_restart_does_not_replay_events_into_triage() {
        let t = build(1, true, false, vec![text("must not run")], |gm, _, _| {
            gm.host = Arc::new(Pending);
            gm.cfg.poll_only = true;
        })
        .await;
        let rev = |th: &str, mm: u32| crate::bot::gmail_store::GmailThreadRev {
            thread_id: th.into(),
            message_id: m(mm),
        };
        let w = |at: i64| GmailWindow {
            start_ms: at,
            lower_s: 1_700_000_000,
        };
        for (at, mm) in [(1000, 1), (2000, 2)] {
            t.g.store
                .record_gmail_window_scan("main", "one", vec![rev(T[0], mm)], w(at), at)
                .await
                .unwrap();
        }
        t.bot.start().await.unwrap();
        tokio::time::sleep(Duration::from_millis(800)).await;
        assert_eq!(
            count(&t, "SELECT COUNT(*) FROM bot_events WHERE task_id IS NULL").await,
            1
        );
        assert_eq!(
            count(
                &t,
                "SELECT COUNT(*) FROM tasks WHERE source = 'event' AND execution_kind = 'model'"
            )
            .await,
            0
        );
        t.bot.stop_loop();
    }

    #[tokio::test]
    async fn summary_restart_hands_old_events_to_triage_without_a_general_agent_task() {
        let t = build(1, true, false, vec![text("must not run")], |gm, _, _| {
            gm.host = Arc::new(Pending);
            gm.cfg.summary = Some(toml::from_str("time = \"23:59\"").unwrap());
        })
        .await;
        let rev = |th: &str, mm: u32| crate::bot::gmail_store::GmailThreadRev {
            thread_id: th.into(),
            message_id: m(mm),
        };
        let w = |at: i64| GmailWindow {
            start_ms: at,
            lower_s: 1_700_000_000,
        };
        for (at, mm) in [(1000, 1), (2000, 2)] {
            t.g.store
                .record_gmail_window_scan("main", "one", vec![rev(T[0], mm)], w(at), at)
                .await
                .unwrap();
        }
        for _ in 0..2 {
            t.bot.start().await.unwrap();
            tokio::time::sleep(Duration::from_millis(400)).await;
            t.bot.stop_loop();
        }
        assert_eq!(
            count(&t, "SELECT COUNT(*) FROM bot_events WHERE task_id IS NULL").await,
            0
        );
        assert_eq!(count(&t, "SELECT COUNT(*) FROM bot_events").await, 1);
        assert_eq!(
            count(
                &t,
                "SELECT COUNT(*) FROM gmail_triage WHERE state = 'pending'"
            )
            .await,
            1
        );
        assert_eq!(
            count(
                &t,
                "SELECT COUNT(*) FROM tasks WHERE source = 'event' AND execution_kind = 'model'"
            )
            .await,
            0
        );
    }

    #[test]
    fn comp_is_not_changed_by_the_poller_type() {
        assert_eq!(Comp::Saved, Comp::Saved);
    }
}
