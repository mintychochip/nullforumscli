use scraper::{Html, Selector};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct Author {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub username: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "userId")]
    pub user_id: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct Resource {
    pub id: Option<i64>,
    pub url: String,
    pub title: String,
    pub author: Author,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
    #[serde(rename = "createdAt", skip_serializing_if = "Option::is_none")]
    pub created_at: Option<String>,
    #[serde(rename = "lastUpdated", skip_serializing_if = "Option::is_none")]
    pub last_updated: Option<String>,
    #[serde(rename = "bodyText")]
    pub body_text: String,
}

fn text(el: &scraper::element_ref::ElementRef) -> String {
    el.text().collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Parse a resource page. The title element also contains label/version
/// spans; they're decomposed by reading only the direct text.
pub fn parse_resource(html: &str, url: &str, base_url: &str) -> anyhow::Result<Resource> {
    let doc = Html::parse_document(html);
    if detect_wall(&doc) {
        anyhow::bail!("AUTH_REQUIRED: the site returned a login wall");
    }
    let title_node = doc
        .select(&Selector::parse(".p-title-value").unwrap())
        .next()
        .ok_or_else(|| anyhow::anyhow!("PARSE_FAILURE: no .p-title-value"))?;
    let version = title_node
        .select(&Selector::parse(".u-muted").unwrap())
        .next()
        .map(|v| text(&v));

    // Title text minus the label/version children.
    let mut title = String::new();
    for child in title_node.children() {
        if let Some(t) = child.value().as_text() {
            title.push_str(t);
            title.push(' ');
        }
    }
    let title = title.split_whitespace().collect::<Vec<_>>().join(" ");
    anyhow::ensure!(!title.is_empty(), "PARSE_FAILURE: empty resource title");

    let id = url
        .trim_end_matches('/')
        .rsplit('.')
        .next()
        .and_then(|s| s.split('/').next())
        .and_then(|s| s.parse::<i64>().ok());

    // Author from .p-description a.username (href members/slug.id)
    let mut author = Author { username: None, user_id: None, url: None };
    if let Some(desc) = doc.select(&Selector::parse(".p-description").unwrap()).next() {
        if let Some(a) = desc.select(&Selector::parse("a.username").unwrap()).next() {
            author.username = Some(text(&a));
            if let Some(href) = a.value().attr("href") {
                let full = if href.starts_with("http") {
                    href.to_string()
                } else {
                    format!("{base_url}{href}")
                };
                author.user_id = full
                    .trim_end_matches('/')
                    .rsplit('.')
                    .next()
                    .and_then(|s| s.parse::<i64>().ok());
                author.url = Some(full);
            }
        }
    }

    let sel_time = Selector::parse("time.u-dt").unwrap();
    let times: Vec<String> = doc
        .select(&sel_time)
        .filter_map(|t| t.value().attr("datetime").map(str::to_string))
        .collect();
    let body_sel = Selector::parse(".resourceBody .bbWrapper").unwrap();
    let body_text = doc
        .select(&body_sel)
        .next()
        .map(|b| text(&b))
        .unwrap_or_default();

    Ok(Resource {
        id,
        url: url.to_string(),
        title,
        author,
        version,
        created_at: times.first().cloned(),
        last_updated: times.last().cloned(),
        body_text,
    })
}

fn detect_wall(doc: &Html) -> bool {
    let sel_err = Selector::parse(".blockMessage.error").unwrap();
    for node in doc.select(&sel_err) {
        let t = node.text().collect::<String>().to_lowercase();
        if ["you must log in", "log in or register", "you do not have permission",
            "insufficient privileges", "you must be a registered member"]
            .iter().any(|m| t.contains(m))
        {
            return true;
        }
    }
    false
}
