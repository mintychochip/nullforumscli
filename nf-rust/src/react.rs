use anyhow::{anyhow, Result};
use scraper::{Html, Selector};

use crate::http::Session;

/// One row of /account/reactions-given.
#[derive(Debug, serde::Serialize)]
pub struct ReactionItem {
    #[serde(rename = "reactByUserId", skip_serializing_if = "Option::is_none")]
    pub react_by_user_id: Option<i64>,
    #[serde(rename = "reactByUsername", skip_serializing_if = "Option::is_none")]
    pub react_by_username: Option<String>,
    #[serde(rename = "targetUrl", skip_serializing_if = "Option::is_none")]
    pub target_url: Option<String>,
    #[serde(rename = "targetTitle", skip_serializing_if = "Option::is_none")]
    pub target_title: Option<String>,
    #[serde(rename = "reactionId", skip_serializing_if = "Option::is_none")]
    pub reaction_id: Option<i64>,
    #[serde(rename = "reactedAt", skip_serializing_if = "Option::is_none")]
    pub reacted_at: Option<String>,
}

/// First react anchor on a page -> (path, already_liked, reaction_id).
/// `has-reaction` in the anchor class means this session already reacted.
pub fn react_anchor(html: &str) -> Option<(String, bool, i64)> {
    let doc = Html::parse_document(html);
    let sel = Selector::parse(r#"a[href*="react?reaction_id="]"#).unwrap();
    for node in doc.select(&sel) {
        let href = node.value().attr("href")?;
        if !href.contains("/posts/") && !href.contains("/resources/") {
            continue;
        }
        let classes = node.value().attr("class").unwrap_or("");
        let rid = href
            .split("reaction_id=")
            .nth(1)
            .and_then(|v| v.split('&').next())
            .and_then(|v| v.parse().ok());
        return Some((href.to_string(), classes.contains("has-reaction"), rid.unwrap_or(1)));
    }
    None
}

/// Per-page `_xfToken` (form input). Required for every react POST.
pub fn extract_token(html: &str) -> Result<String> {
    let doc = Html::parse_document(html);
    let sel = Selector::parse(r#"input[name="_xfToken"]"#).unwrap();
    doc.select(&sel)
        .find_map(|n| n.value().attr("value").map(str::to_string))
        .ok_or_else(|| anyhow!("no _xfToken in page; session may be stale"))
}

/// Like a post or resource. Returns {"status": "liked"|"already", ...}.
pub async fn do_like(s: &mut Session, target_url: &str) -> Result<serde_json::Value> {
    let page = s.get(target_url).await?;
    let token = extract_token(&page)?;
    let anchor = react_anchor(&page)
        .ok_or_else(|| anyhow!("no reaction anchor on the target page"))?;
    let (path, already, rid) = anchor;
    if already {
        return Ok(serde_json::json!({
            "status": "already", "reactionId": rid, "target": target_url
        }));
    }
    let result = s
        .post_form(&path, &[
            ("_xfToken", token.as_str()),
            ("_xfWithData", "1"),
            ("_xfResponseType", "json"),
        ])
        .await?;
    let errors = result.get("errors").and_then(|e| e.as_array()).cloned();
    for err in errors.unwrap_or_default() {
        let text = err.as_str().unwrap_or("").to_string();
        if text.to_lowercase().contains("cancel this reaction") {
            return Ok(serde_json::json!({
                "status": "already", "reactionId": rid, "target": target_url
            }));
        }
        return Err(anyhow!("react failed: {text}"));
    }
    if result.get("status").and_then(|s| s.as_str()) == Some("error") {
        return Err(anyhow!("react failed: {result}"));
    }
    Ok(serde_json::json!({"status": "liked", "target": target_url}))
}

/// Parse one page of /account/reactions-given (fetched via get_own).
pub fn parse_reactions(html: &str, base_url: &str) -> (Vec<ReactionItem>, Option<i64>) {
    let doc = Html::parse_document(html);
    let mut items = Vec::new();
    let sel_row = Selector::parse("li.block-row.block-row--separated").unwrap();
    let sel_target = Selector::parse("div.contentRow-title a[href]:not([href*='/members/'])").unwrap();
    let sel_user = Selector::parse("a.username[data-user-id]").unwrap();
    let sel_rid = Selector::parse("[data-reaction-id]").unwrap();
    let sel_time = Selector::parse("time.u-dt").unwrap();
    for row in doc.select(&sel_row) {
        let user = row.select(&sel_user).next();
        let (name, uid) = match user {
            Some(u) => (
                Some(u.text().collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ")),
                u.value().attr("data-user-id").and_then(|v| v.parse().ok()),
            ),
            None => (None, None),
        };
        let target = row.select(&sel_target).next();
        let (target_url, target_title) = match target {
            Some(a) => {
                let href = a.value().attr("href").unwrap_or("");
                let full = if href.starts_with("http") {
                    href.to_string()
                } else {
                    format!("{base_url}{href}")
                };
                (Some(full), Some(a.text().collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ")))
            }
            None => (None, None),
        };
        let reaction_id = row.select(&sel_rid).next().and_then(|r| {
            r.value().attr("data-reaction-id").and_then(|v| v.parse().ok())
        });
        let reacted_at = row.select(&sel_time).next().and_then(|t| {
            t.value().attr("datetime").map(|d| {
                // +-HHMM -> +-HH:MM (same normalization as Python)
                if d.len() >= 5 && !d.contains(':') {
                    format!("{}:{}", &d[..d.len() - 2], &d[d.len() - 2..])
                } else {
                    d.to_string()
                }
            })
        });
        items.push(ReactionItem {
            react_by_user_id: uid,
            react_by_username: name,
            target_url,
            target_title,
            reaction_id,
            reacted_at,
        });
    }
    // total from the tab bar: "All (N)"
    let total = doc
        .select(&Selector::parse("a.tabs-tab").unwrap())
        .find_map(|n| {
            let t = n.text().collect::<String>();
            let t = t.split_whitespace().collect::<Vec<_>>().join(" ");
            t.strip_prefix("All (")
                .and_then(|r| r.trim_end_matches(')').parse::<i64>().ok())
        });
    (items, total)
}
