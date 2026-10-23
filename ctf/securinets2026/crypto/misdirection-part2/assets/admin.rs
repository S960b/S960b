use crate::startup::var;
use axum::Json;
use serde::{Deserialize, Serialize};
use std::process::Command;
use tracing::{error, info};

#[derive(Deserialize)]
pub struct ReportRequest {
    url: String,
}

#[derive(Serialize)]
pub struct ReportResponse {
    status: &'static str,
    message: String,
}

fn reply(status: &'static str, message: impl Into<String>) -> Json<ReportResponse> {
    Json(ReportResponse { status, message: message.into() })
}

fn bot_error(output: &str) -> Option<String> {
    output
        .contains("ERROR:")
        .then(|| output.split("ERROR:").last().unwrap_or_default().trim().to_string())
}

pub async fn report_handler(Json(payload): Json<ReportRequest>) -> Json<ReportResponse> {
    let frontend_url = var("FRONTEND_URL");

    if !payload.url.starts_with(&frontend_url) {
        return reply("error", format!("URL must start with {frontend_url}"));
    }
    info!("Received report for URL: {}", payload.url);

    let bot_url = payload.url.replacen(&frontend_url, &var("BOT_FRONTEND_URL"), 1);

    let output = Command::new("node")
        .current_dir(var("BOT_DIR"))
        .arg("bot.js")
        .arg(&bot_url)
        .env("ADMIN_PRIVATE_KEY", var("ADMIN_PRIVATE_KEY"))
        .env("WEBAUTHN_ORIGIN", &frontend_url)
        .output();

    let out = match output {
        Ok(out) => out,
        Err(e) => {
            error!("Failed to execute bot: {}", e);
            return reply("error", "The Admin bot is currently unavailable.");
        }
    };

    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    info!("Bot stdout: {}", stdout);
    info!("Bot stderr: {}", stderr);

    if stdout.contains("SUCCESS") || stderr.contains("SUCCESS") {
        reply("success", "Admin logged in and everything looks good!")
    } else if let Some(msg) = bot_error(&stdout).or_else(|| bot_error(&stderr)) {
        reply("error", msg)
    } else {
        reply("error", "Admin was confused by your site and left.")
    }
}
