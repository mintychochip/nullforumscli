use scraper::{Html, Selector};

/// Identity fields scraped from any authenticated page's nav block.
#[derive(Debug, Default, serde::Serialize)]
pub struct Account {
    #[serde(rename = "userId")]
    pub user_id: Option<i64>,
    pub username: Option<String>,
    #[serde(rename = "avatarUrl")]
    pub avatar_url: Option<String>,
    #[serde(rename = "creditsBalance")]
    pub credits_balance: Option<i64>,
    #[serde(rename = "levelPoints")]
    pub level_points: Option<i64>,
    #[serde(rename = "levelPointsNeeded")]
    pub level_points_needed: Option<i64>,
    #[serde(rename = "levelNext")]
    pub level_next: Option<i64>,
    #[serde(rename = "levelHint")]
    pub level_hint: Option<String>,
    pub authenticated: bool,
}

/// Visitor identity from the nav (`.p-navgroup-link--user`): avatar
/// carries data-user-id, the anchor's title attr carries the username.
pub fn parse_account(html: &str) -> Option<Account> {
    let doc = Html::parse_document(html);
    let sel = Selector::parse(".p-navgroup-link--user").ok()?;
    let probe = doc.select(&sel).next()?;
    let mut acct = Account { authenticated: true, ..Default::default() };
    if let Some(av) = probe
        .select(&Selector::parse(".avatar").unwrap())
        .next()
        .or_else(|| probe.select(&Selector::parse("span").unwrap()).next())
    {
        acct.user_id = av.value().attr("data-user-id").and_then(|v| v.parse().ok());
    }
    acct.username = probe.value().attr("title").map(str::to_string);
    if let Some(img) = probe.select(&Selector::parse("img").unwrap()).next() {
        acct.avatar_url = img.value().attr("src").map(str::to_string);
    }
    if acct.user_id.is_none() && acct.username.is_none() {
        return None;
    }
    Some(acct)
}

/// Wallet widget: the nav link to /withdraw/ renders "Credits: N".
pub fn parse_wallet(html: &str, acct: &mut Account) {
    let doc = Html::parse_document(html);
    let sel = Selector::parse(r#"a[href*="/withdraw/"]"#).unwrap();
    for node in doc.select(&sel) {
        let text = clean(node.text().collect::<String>());
        if let Some(rest) = text
            .split_once("Credits:")
            .map(|(_, r)| r.trim().trim_end_matches('.').replace(',', ""))
        {
            if let Ok(n) = rest.parse::<i64>() {
                acct.credits_balance = Some(n);
                return;
            }
        }
    }
}

/// Null Level progress off /pages/nullforums-level-system/:
/// three spans `#nfLvlPointData` = points, threshold, next level.
pub fn parse_level_progress(html: &str) -> Option<(i64, i64, i64)> {
    let doc = Html::parse_document(html);
    let sel = Selector::parse("span#nfLvlPointData").unwrap();
    let vals: Vec<i64> = doc
        .select(&sel)
        .filter_map(|n| clean(n.text().collect::<String>()).parse().ok())
        .collect();
    if vals.len() >= 3 {
        Some((vals[0], vals[1], vals[2]))
    } else {
        None
    }
}

fn clean(s: String) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ").trim().to_string()
}

/// The human line explaining what the level numbers mean.
pub fn level_hint(points: i64, needed: i64, next: i64) -> String {
    if points >= needed {
        "level 2 reached: uploads earn $0.10 each (updates $0.05 from L3)".into()
    } else {
        format!("level {next} in {} pts: unlocks $0.10/upload earnings", needed - points)
    }
}
