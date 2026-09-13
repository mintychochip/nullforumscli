use serde::Deserialize;
use std::path::PathBuf;

#[derive(Deserialize, Debug, Default)]
struct FileConfig {
    base_url: Option<String>,
    cookie: Option<String>,
    rate_limit_ms: Option<u64>,
    #[serde(rename = "cacheDir")]
    cache_dir: Option<String>,
    #[serde(rename = "stateDir")]
    state_dir: Option<String>,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct Config {
    pub base_url: String,
    pub cookie: Option<String>,
    pub rate_limit_ms: u64,
    pub cache_dir: PathBuf,
    pub state_dir: PathBuf,
}

impl Config {
    /// env > TOML > defaults. Same precedence as the Python CLI.
    pub fn load() -> anyhow::Result<Self> {
        let path = default_config_path();
        let file: FileConfig = if path.is_file() {
            let raw = std::fs::read_to_string(&path)?;
            toml::from_str(&raw)?
        } else {
            FileConfig::default()
        };

        let base_url = std::env::var("NF_BASE_URL")
            .ok()
            .or(file.base_url)
            .unwrap_or_else(|| "https://nullforums.net".into())
            .trim_end_matches('/')
            .to_string();
        let cookie = std::env::var("NF_COOKIE").ok().or(file.cookie);
        let rate_limit_ms = std::env::var("NF_RATE_LIMIT_MS")
            .ok()
            .and_then(|v| v.parse().ok())
            .or(file.rate_limit_ms)
            .unwrap_or(1000);
        let xdg_cache = std::env::var("XDG_CACHE_HOME")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".cache"));
        let xdg_state = std::env::var("XDG_STATE_HOME")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".local/state"));
        let cache_dir = std::env::var("NF_CACHE_DIR")
            .ok()
            .map(PathBuf::from)
            .or(file.cache_dir.map(PathBuf::from))
            .unwrap_or_else(|| xdg_cache.join("nullforums"));
        let state_dir = std::env::var("NF_STATE_DIR")
            .ok()
            .map(PathBuf::from)
            .or(file.state_dir.map(PathBuf::from))
            .unwrap_or_else(|| xdg_state.join("nullforums"));

        Ok(Self { base_url, cookie, rate_limit_ms, cache_dir, state_dir })
    }
}

fn default_config_path() -> PathBuf {
    if let Ok(p) = std::env::var("NF_CONFIG") {
        return PathBuf::from(p);
    }
    home_dir().join(".config/nullforums/config.toml")
}

fn home_dir() -> PathBuf {
    std::env::var("HOME").map(PathBuf::from).unwrap_or_default()
}
