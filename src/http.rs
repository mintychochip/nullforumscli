use anyhow::{anyhow, Context, Result};
use reqwest::redirect::Policy;
use reqwest::{Client as Httpx, Url};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::config::Config;

pub const MAX_BODY_BYTES: u64 = 16 * 1024 * 1024;
pub const TIMEOUT_SECS: u64 = 20;

/// The single module that opens sockets, mirroring Python `nf.http.Client`:
/// same-origin only, cookie-jar seeding from config, min spacing between
/// requests, HTML gate detection for downloads.
pub struct Session {
    pub http: Httpx,
    pub base_url: String,
    last_request: Option<Instant>,
    rate: Duration,
}

impl Session {
    pub fn new(cfg: &Config) -> Result<Self> {
        let mut builder = Httpx::builder()
            .user_agent(format!(
                "nf/{} (client; +https://github.com/jlo/nullforumscli)",
                env!("CARGO_PKG_VERSION")
            ))
            .redirect(Policy::limited(10))
            .timeout(Duration::from_secs(TIMEOUT_SECS));
        if let Some(cookie) = &cfg.cookie {
            let url: Url = cfg.base_url.parse().context("bad base_url")?;
            let jar = reqwest::cookie::Jar::default();
            for part in cookie.split(';') {
                let part = part.trim();
                if part.is_empty() {
                    continue;
                }
                // reqwest's Jar API needs a Set-Cookie-shaped line per domain.
                let (name, value) = part.split_once('=').unwrap_or((part, ""));
                jar.add_cookie_str(
                    &format!("{name}={value}; Domain={}; Path=/", url.host_str().unwrap_or("")),
                    &url,
                );
            }
            builder = builder.cookie_provider(Arc::new(jar));
        }
        Ok(Self {
            http: builder.build()?,
            base_url: cfg.base_url.clone(),
            last_request: None,
            rate: Duration::from_millis(cfg.rate_limit_ms),
        })
    }

    /// Minimum spacing between any two requests, like the Python client.
    fn throttle(&mut self) {
        if let Some(t) = self.last_request {
            let elapsed = t.elapsed();
            if elapsed < self.rate {
                std::thread::sleep(self.rate - elapsed);
            }
        }
        self.last_request = Some(Instant::now());
    }

    pub async fn get(&mut self, path: &str) -> Result<String> {
        let url = self.resolve(path)?;
        self.throttle();
        let resp = self
            .http
            .get(url.clone())
            .send()
            .await
            .with_context(|| format!("GET {path}"))?;
        let status = resp.status();
        let final_url = resp.url().clone();
        let same_origin = same_origin(&self.base_url, final_url.as_str());
        let text = resp.text().await?;
        if !same_origin {
            return Err(anyhow!("redirect left the configured origin: {final_url}"));
        }
        if status.as_u16() == 403 || status.as_u16() == 503 || looks_blocked(&text) {
            return Err(anyhow!("blocked at the edge (status {})", status));
        }
        Ok(text)
    }

    /// POST a XenForo AJAX form. Never cached; returns parsed JSON.
    pub async fn post_form(&mut self, path: &str, form: &[(&str, &str)]) -> Result<serde_json::Value> {
        let url = self.resolve(path)?;
        self.throttle();
        let resp = self
            .http
            .post(url)
            .header("X-Requested-With", "XMLHttpRequest")
            .header("Accept", "application/json, text/javascript, */*; q=0.01")
            .form(form)
            .send()
            .await
            .with_context(|| format!("POST {path}"))?;
        let status = resp.status();
        let text = resp.text().await?;
        if status.as_u16() == 429 || status.as_u16() >= 500 {
            return Err(anyhow!("server error {} after POST", status));
        }
        if looks_blocked(&text) && status.as_u16() >= 400 {
            return Err(anyhow!("blocked at the edge (status {})", status));
        }
        match serde_json::from_str::<serde_json::Value>(&text) {
            Ok(v) => Ok(v),
            Err(_) if status.is_success() => Ok(serde_json::json!({
                "status": "ok", "status_code": status.as_u16(), "text": text
            })),
            Err(e) => Err(anyhow!("POST {path}: non-JSON response ({}): {}", status, e)),
        }
    }

    /// Documented robots override for a strictly own-data path: /account/*.
    /// The only like-history source lives there, robots-disallowed for
    /// crawlers; this fetches exactly one operator-chosen /account/ URL
    /// under the operator's session, and refuses anything else.
    pub async fn get_own(&mut self, path: &str) -> Result<String> {
        anyhow::ensure!(
            path.starts_with("/account/"),
            "get_own is only for own-account paths: /account/..."
        );
        self.get(path).await
    }

    pub async fn head(&mut self, path: &str) -> Result<reqwest::header::HeaderMap> {
        let url = self.resolve(path)?;
        self.throttle();
        let resp = self
            .http
            .head(url)
            .send()
            .await
            .with_context(|| format!("HEAD {path}"))?;
        Ok(resp.headers().clone())
    }

    /// GET to a file path, streamed to disk (bypasses the HTML text path).
    pub async fn download(&mut self, path: &str, dest: &std::path::Path) -> Result<u64> {
        let url = self.resolve(path)?;
        self.throttle();
        let resp = self
            .http
            .get(url)
            .send()
            .await
            .with_context(|| format!("GET {path}"))?;
        let status = resp.status();
        let ctype = resp
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let disposition = resp
            .headers()
            .get(reqwest::header::CONTENT_DISPOSITION)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        if !status.is_success() {
            return Err(anyhow!("download returned {status}"));
        }
        if ctype.starts_with("text/html") || disposition.is_empty() {
            return Err(anyhow!(
                "gate page instead of a file; like the resource first: nf like <resource>"
            ));
        }
        use std::io::Write;
        if let Some(parent) = dest.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut file = std::fs::File::create(dest)?;
        let mut nbytes = 0u64;
        let mut resp = resp;
        while let Some(chunk) = resp.chunk().await? {
            nbytes += chunk.len() as u64;
            if nbytes > MAX_BODY_BYTES {
                return Err(anyhow!("download exceeds {MAX_BODY_BYTES} bytes"));
            }
            file.write_all(&chunk)?;
        }
        Ok(nbytes)
    }

    fn resolve(&self, path: &str) -> Result<Url> {
        let url = if path.starts_with("http") {
            Url::parse(path)?
        } else {
            Url::parse(&format!("{}{}", self.base_url, path))?
        };
        if !same_origin(&self.base_url, url.as_str()) {
            return Err(anyhow!("refusing off-origin URL: {url}"));
        }
        Ok(url)
    }
}

fn same_origin(base: &str, other: &str) -> bool {
    match (Url::parse(base), Url::parse(other)) {
        (Ok(b), Ok(o)) => {
            b.host_str() == o.host_str() && b.port_or_known_default() == o.port_or_known_default()
        }
        _ => false,
    }
}

fn looks_blocked(text: &str) -> bool {
    let head = &text[..text.len().min(4000)].to_lowercase();
    ["just a moment", "attention required", "challenge-platform", "cf-chl"]
        .iter()
        .any(|m| head.contains(m))
}
