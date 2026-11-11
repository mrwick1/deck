//! Streaming client for an OpenAI-compatible chat completions endpoint.

use std::time::Duration;

use anyhow::{Context, anyhow};
use futures_util::{Stream, StreamExt};
use serde_json::{Value, json};

#[derive(Debug, Clone, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Delta {
    Text(String),
    /// A tool call with all argument fragments joined and parsed.
    ToolCall(ToolCall),
    Finish(String),
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

    pub fn has_key(&self) -> bool {
        self.key.is_some()
    }

    /// Stream one chat completion. Tool calls arrive whole, after the text.
    pub fn stream(
        &self,
        model: &str,
        messages: &[Value],
        tools: &[Value],
    ) -> impl Stream<Item = anyhow::Result<Delta>> + Send + use<> {
        let this = self.clone();
        let mut body = json!({ "model": model, "messages": messages, "stream": true });
        if !tools.is_empty() {
            body["tools"] = json!(tools);
        }
        async_stream::try_stream! {
            let key = this.key.as_deref().ok_or_else(|| {
                anyhow!("model key is missing: set the environment variable {}", this.key_env)
            })?;
            let res = this
                .http
                .post(format!("{}/chat/completions", this.base_url))
                .bearer_auth(key)
                .timeout(Duration::from_secs(120))
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
            let mut calls: Vec<PendingCall> = Vec::new();
            let mut finish: Option<String> = None;
            let mut done = false;
            while !done {
                let Some(chunk) = bytes.next().await else { break };
                buf.extend_from_slice(&chunk.context("model stream failed")?);
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
            yield Delta::Finish(finish.unwrap_or_else(|| "stop".into()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{fake_model, sse};

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

    #[tokio::test]
    async fn missing_key_is_a_clear_error() {
        let c = ModelClient::new("http://127.0.0.1:1", None, "DECK_MODEL_KEY");
        let mut s = Box::pin(c.stream("m", &[], &[]));
        let e = s.next().await.unwrap().unwrap_err().to_string();
        assert!(e.contains("DECK_MODEL_KEY"));
    }
}
