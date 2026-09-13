use scraper::{Html, Selector};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct Author {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub username: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "userId")]
    pub user_id: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct Post {
    pub id: Option<i64>,
    pub index: usize,
    pub author: Author,
    #[serde(rename = "postedAt", skip_serializing_if = "Option::is_none")]
    pub posted_at: Option<String>,
    #[serde(rename = "bodyText")]
    pub body_text: String,
}

#[derive(Debug, Serialize)]
pub struct Thread {
    pub id: Option<i64>,
    pub url: String,
    pub title: String,
    #[serde(rename = "createdAt", skip_serializing_if = "Option::is_none")]
    pub created_at: Option<String>,
    #[serde(rename = "updatedAt", skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<String>,
    pub posts: Vec<Post>,
    pub pagination: Pagination,
}

#[derive(Debug, Serialize)]
pub struct Pagination {
    page: u32,
    pages: u32,
}

fn text(el: &scraper::element_ref::ElementRef) -> String {
    el.text().collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Parse a thread page. XenForo hooks only (article.message[data-content],
/// .p-title-value, time.u-dt) so theme restyles don't break it.
pub fn parse_thread(html: &str, url: &str) -> anyhow::Result<Thread> {
    let doc = Html::parse_document(html);
    if detect_wall(&doc) {
        anyhow::bail!("AUTH_REQUIRED: the site returned a login wall");
    }
    let title = doc
        .select(&Selector::parse(".p-title-value").unwrap())
        .next()
        .map(|n| text(&n))
        .unwrap_or_default();
    anyhow::ensure!(!title.is_empty(), "PARSE_FAILURE: no .p-title-value found");

    let id = url.split(".")
        .last()
        .and_then(|tail| tail.trim_end_matches('/').parse::<i64>().ok())
        .or_else(|| {
            url.trim_end_matches('/')
                .rsplit('/')
                .next()
                .and_then(|seg| seg.split('.').last())
                .and_then(|seg| seg.parse::<i64>().ok())
        });

    let mut posts = Vec::new();
    let sel_post = Selector::parse("article.message[data-content]").unwrap();
    let sel_author = Selector::parse("a.username").unwrap();
    let sel_time = Selector::parse("time.u-dt").unwrap();
    let sel_body = Selector::parse(".bbWrapper").unwrap();
    for (idx, node) in doc.select(&sel_post).enumerate() {
        let id_attr = node.value().attr("data-content").unwrap_or("");
        let post_id = id_attr.trim_start_matches("post-").parse::<i64>().ok();
        let author = node
            .select(&sel_author)
            .next()
            .map(|a| text(&a))
            .unwrap_or_default();
        let posted_at = node
            .select(&sel_time)
            .next()
            .and_then(|tm| tm.value().attr("datetime"))
            .map(str::to_string);
        let body_text = node
            .select(&sel_body)
            .next()
            .map(|b| text(&b))
            .unwrap_or_default();
        posts.push(Post {
            id: post_id,
            index: idx + 1,
            author: Author { username: Some(author), user_id: None },
            posted_at,
            body_text,
        });
    }
    anyhow::ensure!(!posts.is_empty(), "PARSE_FAILURE: no posts found");

    let times: Vec<String> = doc
        .select(&sel_time)
        .filter_map(|tm| tm.value().attr("datetime").map(str::to_string))
        .collect();

    Ok(Thread {
        id,
        url: url.to_string(),
        title,
        created_at: times.first().cloned(),
        updated_at: times.last().cloned(),
        posts,
        pagination: pagination(&doc),
    })
}

fn pagination(doc: &Html) -> Pagination {
    let sel_nav = Selector::parse(".pageNav").unwrap();
    let nav = match doc.select(&sel_nav).next() {
        Some(n) => n,
        None => return Pagination { page: 1, pages: 1 },
    };
    let page = nav
        .select(&Selector::parse(".pageNav-page--current a").unwrap())
        .next()
        .and_then(|a| text(&a).parse().ok())
        .unwrap_or(1);
    let pages = nav
        .select(&Selector::parse(".pageNav-page a").unwrap())
        .filter_map(|a| text(&a).parse::<u32>().ok())
        .max()
        .unwrap_or(1);
    Pagination { page, pages }
}

fn detect_wall(doc: &Html) -> bool {
    let sel_err = Selector::parse(".blockMessage.error").unwrap();
    for node in doc.select(&sel_err) {
        let t = text(&node).to_lowercase();
        if ["you must log in", "log in or register", "you do not have permission",
            "insufficient privileges", "you must be a registered member"]
            .iter().any(|m| t.contains(m))
        {
            return true;
        }
    }
    doc.select(&Selector::parse(r#"form[action="/login/login"]"#).unwrap())
        .next()
        .is_some()
}
