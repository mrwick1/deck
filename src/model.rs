//! Streaming client for an OpenAI-compatible chat completions endpoint.
//!
//! `stream` keeps the plain behavior. `stream_with` adds explicit request
//! options. `stream_accounted` is the accounted entry: it admits the call with
//! an `Accountant` before dispatch and settles it once afterwards.

// The accounting seam has no production caller until the ledger lands.
#![allow(dead_code)]

use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, anyhow};
use futures_util::future::BoxFuture;
use futures_util::{Stream, StreamExt};
use serde_json::{Value, json};

#[derive(Debug, Clone, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: Value,
}

/// Token counts the provider reported. Chunks may carry parts of it; the
/// latest value of each field wins. A field that is present but not a
/// non-negative integer sets `malformed`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Usage {
    pub prompt_tokens: Option<u64>,
    pub completion_tokens: Option<u64>,
    pub cached_tokens: Option<u64>,
    pub reasoning_tokens: Option<u64>,
    pub malformed: bool,
}

impl Usage {
    /// `(prompt, completion)` when the provider reported both and the data is sound.
    pub fn complete(&self) -> Option<(u64, u64)> {
        match (self.prompt_tokens, self.completion_tokens, self.malformed) {
            (Some(p), Some(c), false) => Some((p, c)),
            _ => None,
        }
    }

    fn merge(&mut self, v: &Value) {
        fn field(v: &Value, slot: &mut Option<u64>, bad: &mut bool) {
            match v {
                Value::Null => {}
                v => match v.as_u64() {
                    Some(n) => *slot = Some(n),
                    None => *bad = true,
                },
            }
        }
        field(
            &v["prompt_tokens"],
            &mut self.prompt_tokens,
            &mut self.malformed,
        );
        field(
            &v["completion_tokens"],
            &mut self.completion_tokens,
            &mut self.malformed,
        );
        field(
            &v["prompt_tokens_details"]["cached_tokens"],
            &mut self.cached_tokens,
            &mut self.malformed,
        );
        field(
            &v["completion_tokens_details"]["reasoning_tokens"],
            &mut self.reasoning_tokens,
            &mut self.malformed,
        );
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Delta {
    Text(String),
    /// A tool call with all argument fragments joined and parsed.
    ToolCall(ToolCall),
    /// Usage the provider reported, merged over the whole stream. Comes once, before `Finish`, and only when the provider sent any.
    Usage(Usage),
    Finish(String),
}

/// Which JSON field bounds the output. Providers differ; configure it per provider.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub enum TokenField {
    #[default]
    MaxTokens,
    MaxCompletionTokens,
}

/// How to ask the provider for usage on a stream.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub enum UsageRequest {
    /// Send nothing extra. The provider may still report usage.
    #[default]
    Off,
    /// Send `stream_options.include_usage`.
    StreamOptions,
}

/// Explicit limits and provider compatibility for one request. The default
/// changes nothing in the request and keeps the old 120 s timeout.
#[derive(Debug, Clone, PartialEq)]
pub struct RequestOptions {
    pub max_output_tokens: Option<u32>,
    pub token_field: TokenField,
    pub usage: UsageRequest,
    pub timeout: Duration,
    /// Refuse a response that streams more than this many bytes.
    pub max_response_bytes: Option<usize>,
}

impl Default for RequestOptions {
    fn default() -> Self {
        Self {
            max_output_tokens: None,
            token_field: TokenField::default(),
            usage: UsageRequest::default(),
            timeout: Duration::from_secs(120),
            max_response_bytes: None,
        }
    }
}

impl RequestOptions {
    /// An accounted call needs a bound on output and on the response size.
    fn bounded(&self) -> anyhow::Result<u32> {
        match (self.max_output_tokens, self.max_response_bytes) {
            (Some(n), Some(_)) if n > 0 => Ok(n),
            _ => Err(anyhow!(
                "an accounted model call needs max_output_tokens above zero and max_response_bytes"
            )),
        }
    }
}

/// Facts about one call, given to the accountant before dispatch. The accountant
/// owns pricing and the reservation size; this carries no price.
#[derive(Debug, Clone)]
pub struct CallRequest {
    /// Unique per call. The ledger must key reservation and settlement on it.
    pub request_id: String,
    pub model: String,
    /// Byte length of the JSON body that will be sent.
    pub input_bytes: usize,
    pub max_output_tokens: u32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Ticket {
    pub request_id: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Admission {
    Admitted(Ticket),
    /// No dispatch. The reason is for the owner and the logs.
    Hold(String),
}

#[derive(Debug, Clone, PartialEq)]
pub enum UnknownReason {
    /// The stream ended and the provider reported no complete usage.
    MissingUsage,
    /// Error after dispatch (network, HTTP status, bad chunk, size limit).
    Failed(String),
    /// The consumer dropped the stream before the end.
    Abandoned,
}

/// How a dispatched call ended. There is no "free" outcome: every outcome after
/// admission is either reported usage or unresolved exposure.
#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    Reported(Usage),
    Unknown(UnknownReason),
}

/// The admission and settlement port. The durable ledger implements it.
/// `settle` must be idempotent per `request_id`. A ticket that is admitted but
/// never settled (crash, failed `settle`) must stay reserved as unresolved exposure.
pub trait Accountant: Send + Sync {
    fn admit(&self, req: &CallRequest) -> BoxFuture<'_, anyhow::Result<Admission>>;
    fn settle(&self, ticket: &Ticket, outcome: Outcome) -> BoxFuture<'_, anyhow::Result<()>>;
}

/// Accounting that is not configured. It holds every call, so a missing policy never means zero cost.
pub struct Unconfigured;

impl Accountant for Unconfigured {
    fn admit(&self, _: &CallRequest) -> BoxFuture<'_, anyhow::Result<Admission>> {
        Box::pin(async { Ok(Admission::Hold("model accounting is not configured".into())) })
    }
    fn settle(&self, _: &Ticket, _: Outcome) -> BoxFuture<'_, anyhow::Result<()>> {
        Box::pin(async { Ok(()) })
    }
}

/// The accountant held the call. Callers treat it as a hold, not a failure.
#[derive(Debug)]
pub struct AccountingHold(pub String);

impl std::fmt::Display for AccountingHold {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "model call held: {}", self.0)
    }
}

impl std::error::Error for AccountingHold {}

/// Settles as `Abandoned` when dropped before a settlement.
struct SettleGuard {
    accountant: Arc<dyn Accountant>,
    ticket: Option<Ticket>,
}

impl SettleGuard {
    async fn settle(&mut self, outcome: Outcome) {
        let Some(t) = self.ticket.take() else { return };
        if let Err(e) = self.accountant.settle(&t, outcome).await {
            tracing::warn!("model settlement failed; exposure stays reserved: {e:#}");
        }
    }
}

impl Drop for SettleGuard {
    fn drop(&mut self) {
        let Some(t) = self.ticket.take() else { return };
        let a = self.accountant.clone();
        if let Ok(h) = tokio::runtime::Handle::try_current() {
            h.spawn(async move {
                if let Err(e) = a
                    .settle(&t, Outcome::Unknown(UnknownReason::Abandoned))
                    .await
                {
                    tracing::warn!("model settlement failed; exposure stays reserved: {e:#}");
                }
            });
        }
    }
}

#[derive(Clone)]
pub struct ModelClient {
    http: reqwest::Client,
    base_url: String,
    key: Option<String>,
    key_env: String,
}

#[derive(Default)]
struct PendingCall {
    id: String,
    name: String,
    args: String,
}

impl ModelClient {
    /// `key` is the value of the environment variable named `key_env`.
    pub fn new(base_url: &str, key: Option<String>, key_env: &str) -> Self {
        Self {
            http: reqwest::Client::new(),
            base_url: base_url.trim_end_matches('/').to_owned(),
            key,
            key_env: key_env.to_owned(),
        }
    }

    fn missing_key(&self) -> anyhow::Error {
        anyhow!(
            "model key is missing: set the environment variable {}",
            self.key_env
        )
    }

    fn body(model: &str, messages: &[Value], tools: &[Value], opts: &RequestOptions) -> Value {
        let mut body = json!({ "model": model, "messages": messages, "stream": true });
        if !tools.is_empty() {
            body["tools"] = json!(tools);
        }
        if let Some(n) = opts.max_output_tokens {
            let field = match opts.token_field {
                TokenField::MaxTokens => "max_tokens",
                TokenField::MaxCompletionTokens => "max_completion_tokens",
            };
            body[field] = json!(n);
        }
        if opts.usage == UsageRequest::StreamOptions {
            body["stream_options"] = json!({ "include_usage": true });
        }
        body
    }

    /// Stream one chat completion. Tool calls arrive whole, after the text.
    pub fn stream(
        &self,
        model: &str,
        messages: &[Value],
        tools: &[Value],
    ) -> impl Stream<Item = anyhow::Result<Delta>> + Send + use<> {
        self.stream_with(model, messages, tools, &RequestOptions::default())
    }

    /// Like `stream`, with explicit options. No accounting.
    pub fn stream_with(
        &self,
        model: &str,
        messages: &[Value],
        tools: &[Value],
        opts: &RequestOptions,
    ) -> impl Stream<Item = anyhow::Result<Delta>> + Send + use<> {
        self.send(Self::body(model, messages, tools, opts), opts.clone())
    }

    /// Admit the call, dispatch it, settle it once. A held call never reaches
    /// the model: the first item is an `AccountingHold` error. An unbounded
    /// `opts` is refused before admission. A missing key fails before admission
    /// too, because nothing was sent.
    pub fn stream_accounted(
        &self,
        model: &str,
        messages: &[Value],
        tools: &[Value],
        opts: &RequestOptions,
        accountant: Arc<dyn Accountant>,
    ) -> impl Stream<Item = anyhow::Result<Delta>> + Send + use<> {
        let this = self.clone();
        let body = Self::body(model, messages, tools, opts);
        let opts = opts.clone();
        let model = model.to_owned();
        async_stream::try_stream! {
            let max_output_tokens = opts.bounded()?;
            if this.key.is_none() {
                Err(this.missing_key())?;
            }
            let req = CallRequest {
                request_id: uuid::Uuid::new_v4().to_string(),
                model,
                input_bytes: body.to_string().len(),
                max_output_tokens,
            };
            let ticket = match accountant.admit(&req).await.context("model admission failed")? {
                Admission::Admitted(t) => t,
                Admission::Hold(reason) => Err(AccountingHold(reason))?,
            };
            let mut guard = SettleGuard { accountant: accountant.clone(), ticket: Some(ticket) };
            let mut usage: Option<Usage> = None;
            let mut inner = Box::pin(this.send(body, opts));
            while let Some(next) = inner.next().await {
                match next {
                    Err(e) => {
                        guard.settle(Outcome::Unknown(UnknownReason::Failed(format!("{e:#}")))).await;
                        Err(e)?;
                    }
                    Ok(Delta::Usage(u)) => {
                        usage = Some(u.clone());
                        yield Delta::Usage(u);
                    }
                    Ok(Delta::Finish(f)) => {
                        let outcome = match usage.take() {
                            Some(u) if u.complete().is_some() => Outcome::Reported(u),
                            _ => Outcome::Unknown(UnknownReason::MissingUsage),
                        };
                        guard.settle(outcome).await;
                        yield Delta::Finish(f);
                    }
                    Ok(d) => yield d,
                }
            }
            // The inner stream always ends with Finish or an error; this settles a stream that ended otherwise.
            guard.settle(Outcome::Unknown(UnknownReason::MissingUsage)).await;
        }
    }

    fn send(
        &self,
        body: Value,
        opts: RequestOptions,
    ) -> impl Stream<Item = anyhow::Result<Delta>> + Send + use<> {
        let this = self.clone();
        async_stream::try_stream! {
            let key = this.key.as_deref().ok_or_else(|| this.missing_key())?;
            let res = this
                .http
                .post(format!("{}/chat/completions", this.base_url))
                .bearer_auth(key)
                .timeout(opts.timeout)
                .json(&body)
                .send()
                .await
                .context("model request failed")?;
            let status = res.status();
            let res = if status.is_success() {
                res
            } else {
                let text = res.text().await.unwrap_or_default();
                let excerpt: String = text.chars().take(500).collect();
                Err(anyhow!("model returned {status}: {excerpt}"))?
            };
            let mut bytes = res.bytes_stream();
            let mut buf: Vec<u8> = Vec::new();
            let mut received = 0usize;
            let mut calls: Vec<PendingCall> = Vec::new();
            let mut finish: Option<String> = None;
            let mut usage: Option<Usage> = None;
            let mut done = false;
            while !done {
                let Some(chunk) = bytes.next().await else { break };
                let chunk = chunk.context("model stream failed")?;
                received += chunk.len();
                if opts.max_response_bytes.is_some_and(|m| received > m) {
                    Err(anyhow!("model response exceeded the response limit"))?;
                }
                buf.extend_from_slice(&chunk);
                while let Some(pos) = buf.iter().position(|b| *b == b'\n') {
                    let line: Vec<u8> = buf.drain(..=pos).collect();
                    let line = String::from_utf8_lossy(&line);
                    let Some(data) = line.trim().strip_prefix("data:") else { continue };
                    let data = data.trim();
                    if data == "[DONE]" {
                        done = true;
                        break;
                    }
                    let v: Value = serde_json::from_str(data).context("bad model chunk")?;
                    if v["usage"].is_object() {
                        usage.get_or_insert_with(Usage::default).merge(&v["usage"]);
                    }
                    let Some(choice) = v["choices"].get(0) else { continue };
                    if let Some(t) = choice["delta"]["content"].as_str().filter(|t| !t.is_empty()) {
                        yield Delta::Text(t.to_owned());
                    }
                    for tc in choice["delta"]["tool_calls"].as_array().into_iter().flatten() {
                        let i = tc["index"].as_u64().unwrap_or(0) as usize;
                        if calls.len() <= i {
                            calls.resize_with(i + 1, PendingCall::default);
                        }
                        let c = &mut calls[i];
                        if let Some(id) = tc["id"].as_str() {
                            c.id = id.to_owned();
                        }
                        if let Some(n) = tc["function"]["name"].as_str() {
                            c.name.push_str(n);
                        }
                        if let Some(a) = tc["function"]["arguments"].as_str() {
                            c.args.push_str(a);
                        }
                    }
                    if let Some(f) = choice["finish_reason"].as_str() {
                        finish = Some(f.to_owned());
                    }
                }
            }
            for c in calls {
                let arguments = if c.args.trim().is_empty() {
                    json!({})
                } else {
                    serde_json::from_str(&c.args)
                        .with_context(|| format!("bad arguments for tool call {}", c.name))?
                };
                yield Delta::ToolCall(ToolCall { id: c.id, name: c.name, arguments });
            }
            if let Some(u) = usage {
                yield Delta::Usage(u);
            }
            yield Delta::Finish(finish.unwrap_or_else(|| "stop".into()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{fake_model, sse};
    use std::sync::Mutex;

    async fn collect(url: &str) -> anyhow::Result<Vec<Delta>> {
        let c = ModelClient::new(url, Some("k".into()), "K");
        let mut s = Box::pin(c.stream("m", &[json!({"role":"user","content":"hi"})], &[]));
        let mut out = vec![];
        while let Some(d) = s.next().await {
            out.push(d?);
        }
        Ok(out)
    }

    #[tokio::test]
    async fn plain_text_in_three_chunks() {
        let body = sse(&[
            json!({"choices":[{"delta":{"content":"Hel"}}]}),
            json!({"choices":[{"delta":{"content":"lo "}}]}),
            json!({"choices":[{"delta":{"content":"you"},"finish_reason":"stop"}]}),
        ]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        let out = collect(&url).await.unwrap();
        let text: String = out
            .iter()
            .filter_map(|d| match d {
                Delta::Text(t) => Some(t.as_str()),
                _ => None,
            })
            .collect();
        assert_eq!(text, "Hello you");
        assert_eq!(out.last(), Some(&Delta::Finish("stop".into())));
    }

    #[tokio::test]
    async fn tool_call_arguments_in_fragments() {
        let body = sse(&[
            json!({"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"browser_step","arguments":"{\"steps\":"}}]}}]}),
            json!({"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"shot home\"}"}}]},"finish_reason":"tool_calls"}]}),
        ]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        let out = collect(&url).await.unwrap();
        assert_eq!(
            out[0],
            Delta::ToolCall(ToolCall {
                id: "c1".into(),
                name: "browser_step".into(),
                arguments: json!({"steps":"shot home"}),
            })
        );
        assert_eq!(out[1], Delta::Finish("tool_calls".into()));
    }

    #[tokio::test]
    async fn http_error_has_body_excerpt() {
        let (url, _) = fake_model(vec![(500, "x".repeat(900))]).await;
        let e = collect(&url).await.unwrap_err().to_string();
        assert!(e.contains("500"), "{e}");
        assert!(e.contains(&"x".repeat(500)) && !e.contains(&"x".repeat(501)));
    }

    async fn collect_with(url: &str, opts: RequestOptions) -> anyhow::Result<Vec<Delta>> {
        let c = ModelClient::new(url, Some("k".into()), "K");
        let mut s =
            Box::pin(c.stream_with("m", &[json!({"role":"user","content":"hi"})], &[], &opts));
        let mut out = vec![];
        while let Some(d) = s.next().await {
            out.push(d?);
        }
        Ok(out)
    }

    fn usage_of(out: &[Delta]) -> Vec<Usage> {
        out.iter()
            .filter_map(|d| match d {
                Delta::Usage(u) => Some(u.clone()),
                _ => None,
            })
            .collect()
    }

    #[tokio::test]
    async fn usage_only_terminal_chunk_is_kept() {
        let body = sse(&[
            json!({"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}],"usage":null}),
            json!({"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":2,"total_tokens":13}}),
        ]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        let out = collect(&url).await.unwrap();
        let u = usage_of(&out);
        assert_eq!(u.len(), 1);
        assert_eq!(u[0].complete(), Some((11, 2)));
        assert_eq!(out.last(), Some(&Delta::Finish("stop".into())));
        assert!(matches!(out[out.len() - 2], Delta::Usage(_)));
    }

    #[tokio::test]
    async fn fragmented_usage_merges_per_field() {
        let body = sse(&[
            json!({"choices":[{"delta":{"content":"a"}}],"usage":{"prompt_tokens":7}}),
            json!({"choices":[{"delta":{"content":"b"},"finish_reason":"stop"}]}),
            json!({"choices":[],"usage":{"completion_tokens":3,"completion_tokens_details":{"reasoning_tokens":1}}}),
        ]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        let out = collect(&url).await.unwrap();
        let u = usage_of(&out);
        assert_eq!(u.len(), 1);
        assert_eq!(u[0].complete(), Some((7, 3)));
        assert_eq!(u[0].reasoning_tokens, Some(1));
    }

    #[tokio::test]
    async fn missing_or_malformed_usage_is_not_complete() {
        let body = sse(&[json!({"choices":[{"delta":{"content":"a"},"finish_reason":"stop"}]})]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        assert!(usage_of(&collect(&url).await.unwrap()).is_empty());
        let body = sse(&[json!({"choices":[],"usage":{"prompt_tokens":-4,"completion_tokens":2}})]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        let u = usage_of(&collect(&url).await.unwrap());
        assert_eq!(u[0].complete(), None);
    }

    #[tokio::test]
    async fn options_shape_the_request() {
        let (url, seen) = fake_model(vec![(200, sse(&[]))]).await;
        let opts = RequestOptions {
            max_output_tokens: Some(256),
            token_field: TokenField::MaxCompletionTokens,
            usage: UsageRequest::StreamOptions,
            ..RequestOptions::default()
        };
        collect_with(&url, opts).await.unwrap();
        let b = seen.lock().unwrap()[0].clone();
        assert_eq!(b["max_completion_tokens"], 256);
        assert!(b.get("max_tokens").is_none());
        assert_eq!(b["stream_options"]["include_usage"], true);
    }

    #[tokio::test]
    async fn default_options_leave_the_request_unchanged() {
        let (url, seen) = fake_model(vec![(200, sse(&[]))]).await;
        collect(&url).await.unwrap();
        let b = seen.lock().unwrap()[0].clone();
        assert_eq!(b.as_object().unwrap().len(), 3, "{b}");
    }

    #[tokio::test]
    async fn oversized_response_is_refused() {
        let big = "x".repeat(4000);
        let body = sse(&[json!({"choices":[{"delta":{"content":big}}]})]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        let opts = RequestOptions {
            max_response_bytes: Some(1000),
            ..Default::default()
        };
        let e = collect_with(&url, opts).await.unwrap_err().to_string();
        assert!(e.contains("response limit"), "{e}");
    }

    #[derive(Default)]
    struct Recorder {
        admits: Mutex<Vec<(String, usize, u32)>>,
        settled: Mutex<Vec<(String, Outcome)>>,
        hold: bool,
    }

    impl Accountant for Recorder {
        fn admit(
            &self,
            r: &CallRequest,
        ) -> futures_util::future::BoxFuture<'_, anyhow::Result<Admission>> {
            self.admits.lock().unwrap().push((
                r.request_id.clone(),
                r.input_bytes,
                r.max_output_tokens,
            ));
            let a = if self.hold {
                Admission::Hold("test hold".into())
            } else {
                Admission::Admitted(Ticket {
                    request_id: r.request_id.clone(),
                })
            };
            Box::pin(async move { Ok(a) })
        }
        fn settle(
            &self,
            t: &Ticket,
            o: Outcome,
        ) -> futures_util::future::BoxFuture<'_, anyhow::Result<()>> {
            self.settled.lock().unwrap().push((t.request_id.clone(), o));
            Box::pin(async { Ok(()) })
        }
    }

    fn bounded() -> RequestOptions {
        RequestOptions {
            max_output_tokens: Some(100),
            max_response_bytes: Some(100_000),
            usage: UsageRequest::StreamOptions,
            ..Default::default()
        }
    }

    async fn run_accounted(
        url: &str,
        acct: Arc<Recorder>,
        opts: RequestOptions,
    ) -> Vec<anyhow::Result<Delta>> {
        let c = ModelClient::new(url, Some("k".into()), "K");
        let a: Arc<dyn Accountant> = acct;
        let mut s = Box::pin(c.stream_accounted(
            "m",
            &[json!({"role":"user","content":"hi"})],
            &[],
            &opts,
            a,
        ));
        let mut out = vec![];
        while let Some(d) = s.next().await {
            out.push(d);
        }
        out
    }

    #[tokio::test]
    async fn accounted_call_admits_then_settles_reported_usage_once() {
        let body = sse(&[
            json!({"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}),
            json!({"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}),
        ]);
        let (url, seen) = fake_model(vec![(200, body)]).await;
        let acct = Arc::new(Recorder::default());
        let out = run_accounted(&url, acct.clone(), bounded()).await;
        assert!(out.iter().all(|d| d.is_ok()));
        let admits = acct.admits.lock().unwrap();
        assert_eq!(admits.len(), 1);
        assert_eq!(admits[0].2, 100);
        assert_eq!(
            admits[0].1,
            serde_json::to_string(&seen.lock().unwrap()[0])
                .unwrap()
                .len()
        );
        let settled = acct.settled.lock().unwrap();
        assert_eq!(settled.len(), 1);
        assert_eq!(settled[0].0, admits[0].0);
        assert!(matches!(&settled[0].1, Outcome::Reported(u) if u.complete() == Some((5, 1))));
    }

    #[tokio::test]
    async fn accounted_call_without_usage_settles_unknown() {
        let body = sse(&[json!({"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]})]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        let acct = Arc::new(Recorder::default());
        run_accounted(&url, acct.clone(), bounded()).await;
        let settled = acct.settled.lock().unwrap();
        assert_eq!(settled.len(), 1);
        assert!(matches!(
            settled[0].1,
            Outcome::Unknown(UnknownReason::MissingUsage)
        ));
    }

    #[tokio::test]
    async fn lost_response_settles_unknown_never_free() {
        // The server answers 500, then the stream is cut: both leave exposure unknown.
        let (url, _) = fake_model(vec![(500, "boom".into())]).await;
        let acct = Arc::new(Recorder::default());
        let out = run_accounted(&url, acct.clone(), bounded()).await;
        assert!(out.last().unwrap().is_err());
        let settled = acct.settled.lock().unwrap();
        assert_eq!(settled.len(), 1);
        assert!(matches!(
            settled[0].1,
            Outcome::Unknown(UnknownReason::Failed(_))
        ));
    }

    #[tokio::test]
    async fn dropped_stream_settles_unknown() {
        let body = sse(&[json!({"choices":[{"delta":{"content":"hi"}}]})]);
        let (url, _) = fake_model(vec![(200, body)]).await;
        let acct = Arc::new(Recorder::default());
        let c = ModelClient::new(&url, Some("k".into()), "K");
        let a: Arc<dyn Accountant> = acct.clone();
        let opts = bounded();
        let mut s = Box::pin(c.stream_accounted("m", &[], &[], &opts, a));
        let first = s.next().await.unwrap().unwrap();
        assert!(matches!(first, Delta::Text(_)));
        drop(s);
        tokio::time::sleep(Duration::from_millis(100)).await;
        let settled = acct.settled.lock().unwrap();
        assert_eq!(settled.len(), 1);
        assert!(matches!(
            settled[0].1,
            Outcome::Unknown(UnknownReason::Abandoned)
        ));
    }

    #[tokio::test]
    async fn hold_blocks_dispatch() {
        let (url, seen) = fake_model(vec![(200, sse(&[]))]).await;
        let acct = Arc::new(Recorder {
            hold: true,
            ..Default::default()
        });
        let out = run_accounted(&url, acct.clone(), bounded()).await;
        let e = out[0].as_ref().unwrap_err();
        assert!(e.downcast_ref::<AccountingHold>().is_some());
        assert!(
            seen.lock().unwrap().is_empty(),
            "a held call must not reach the model"
        );
        assert!(acct.settled.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn unbounded_options_are_refused_before_admission() {
        let (url, seen) = fake_model(vec![(200, sse(&[]))]).await;
        let acct = Arc::new(Recorder::default());
        let out = run_accounted(&url, acct.clone(), RequestOptions::default()).await;
        assert!(out[0].is_err());
        assert!(acct.admits.lock().unwrap().is_empty());
        assert!(seen.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn unconfigured_accountant_holds_and_never_means_free() {
        let (url, seen) = fake_model(vec![(200, sse(&[]))]).await;
        let c = ModelClient::new(&url, Some("k".into()), "K");
        let a: Arc<dyn Accountant> = Arc::new(Unconfigured);
        let opts = bounded();
        let mut s = Box::pin(c.stream_accounted("m", &[], &[], &opts, a));
        let e = s.next().await.unwrap().unwrap_err();
        assert!(e.downcast_ref::<AccountingHold>().is_some());
        assert!(seen.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn missing_key_does_not_reserve() {
        let c = ModelClient::new("http://127.0.0.1:1", None, "DECK_MODEL_KEY");
        let acct = Arc::new(Recorder::default());
        let a: Arc<dyn Accountant> = acct.clone();
        let opts = bounded();
        let mut s = Box::pin(c.stream_accounted("m", &[], &[], &opts, a));
        let e = s.next().await.unwrap().unwrap_err().to_string();
        assert!(e.contains("DECK_MODEL_KEY"));
        assert!(acct.admits.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn missing_key_is_a_clear_error() {
        let c = ModelClient::new("http://127.0.0.1:1", None, "DECK_MODEL_KEY");
        let mut s = Box::pin(c.stream("m", &[], &[]));
        let e = s.next().await.unwrap().unwrap_err().to_string();
        assert!(e.contains("DECK_MODEL_KEY"));
    }
}
