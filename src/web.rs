use axum::Json;
use axum::http::{HeaderValue, Method, StatusCode, Uri, header};
use axum::response::{IntoResponse, Response};
use rust_embed::RustEmbed;
use serde_json::json;

#[derive(RustEmbed)]
#[folder = "web/dist"]
struct Assets;

/// Serve the embedded web app. Unknown paths get `index.html`, so client routes work.
/// Unknown `/api` paths get a JSON 404.
/// The old `/experience.html` address redirects to the root. The query stays.
pub async fn serve(method: Method, uri: Uri) -> Response {
    if method == Method::GET && uri.path() == "/experience.html" {
        let to = uri.query().map_or("/".to_owned(), |q| format!("/?{q}"));
        return (
            StatusCode::PERMANENT_REDIRECT,
            [
                (header::LOCATION, to),
                (header::CACHE_CONTROL, "no-cache".into()),
            ],
        )
            .into_response();
    }
    let path = uri.path().trim_start_matches('/');
    if path == "api" || path.starts_with("api/") || method != Method::GET {
        return (StatusCode::NOT_FOUND, Json(json!({ "error": "not found" }))).into_response();
    }
    // `/next` is the desktop Home on the new design system. `/next/kit` is its motion and component trial.
    let path = match path {
        "next" => "next.html",
        "next/kit" => "kit.html",
        _ => path,
    };
    let (name, file) = match Assets::get(path) {
        Some(f) if !path.is_empty() => (path, f),
        _ => match Assets::get("index.html") {
            Some(f) => ("index.html", f),
            None => return StatusCode::NOT_FOUND.into_response(),
        },
    };
    let cache = if name.starts_with("assets/") {
        "public, max-age=31536000, immutable"
    } else {
        "no-cache"
    };
    (
        [
            (
                header::CONTENT_TYPE,
                HeaderValue::from_str(file.metadata.mimetype()).unwrap(),
            ),
            (header::CACHE_CONTROL, HeaderValue::from_static(cache)),
        ],
        file.data.into_owned(),
    )
        .into_response()
}
