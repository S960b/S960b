use std::sync::Arc;

use axum::{
    Json, Router,
    extract::{Request, State},
    http::StatusCode,
    middleware::{self, Next},
    response::{Html, Response},
    routing::{get, post},
};
use serde::Serialize;
use tokio::sync::RwLock;

#[derive(Default, Serialize)]
struct AppState {
    race_number: u64,
    timer: usize,
    horses: [f32; 16],
    favorite: usize,

    user_points: u64,
    user_bet: Option<(usize, u64)>,
}

type SharedState = Arc<RwLock<AppState>>;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    std::env::var("FLAG").map_err(|_| "FLAG environment variable is not set")?;

    let state = AppState {
        user_points: 10,
        ..Default::default()
    };
    let state = Arc::new(RwLock::new(state));

    tokio::spawn(race_loop(state.clone()));

    let app = Router::new()
        .route("/", get(index))
        .route(
            "/fonts/m-plus-rounded-1c-700.woff2",
            get(async || include_bytes!("../static/m-plus-rounded-1c-700.woff2")),
        )
        .route(
            "/fonts/m-plus-rounded-1c-800.woff2",
            get(async || include_bytes!("../static/m-plus-rounded-1c-800.woff2")),
        )
        .route("/api/state", get(race_state))
        .route(
            "/api/bet",
            post(bet).route_layer(middleware::from_fn_with_state(state.clone(), betting_open)),
        )
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:80").await?;
    println!("Listening on http://{}", listener.local_addr()?);
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            tokio::signal::ctrl_c().await.ok();
        })
        .await?;
    Ok(())
}

async fn race_loop(state: SharedState) {
    let mut timer = tokio::time::interval(std::time::Duration::from_millis(1000));
    loop {
        timer.tick().await;
        let mut state = state.write().await;

        if state.timer == 0 {
            state.race_number += 1;
            eprintln!("Preparing race {} ", state.race_number);

            state.horses = [0.0; 16];
        } else if state.timer == 30 {
            state.favorite = rand::random_range(0..=15);
            eprintln!(
                "Race {} started, favorite horse: {}",
                state.race_number, state.favorite
            );
        } else if state.timer > 30 {
            for h in 0..16 {
                let base = rand::random_range(0.0..=3.0);
                let speed = if h == state.favorite {
                    base * 2.0
                } else {
                    base
                };
                state.horses[h] += speed;
            }

            if state.horses.iter().any(|&h| h >= 100.0) {
                let winner = state
                    .horses
                    .iter()
                    .enumerate()
                    .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap())
                    .map(|(i, _)| i)
                    .unwrap();

                eprintln!(
                    "Race {} finished! Winner: Horse {}",
                    state.race_number, winner
                );

                if let Some((bet_horse, bet_amount)) = state.user_bet.take() {
                    if bet_horse == winner {
                        state.user_points += bet_amount * 2;
                        eprintln!("You won the bet! New points: {}", state.user_points);
                    } else {
                        eprintln!("You lost the bet. New points: {}", state.user_points);
                    }
                }

                state.timer = 0;
                continue;
            }
        }

        state.timer += 1;
    }
}

async fn betting_open(
    State(state): State<SharedState>,
    request: Request,
    next: Next,
) -> Result<Response, (StatusCode, String)> {
    if state.read().await.timer >= 30 {
        return Err((StatusCode::BAD_REQUEST, "Race already started".to_string()));
    }

    Ok(next.run(request).await)
}

async fn index() -> Html<&'static str> {
    Html(include_str!("../static/index.html"))
}

#[derive(Serialize)]
struct PublicState {
    race_number: u64,
    timer: usize,
    horses: [f32; 16],

    user_points: u64,
    user_bet: Option<(usize, u64)>,

    vip: String,
}

async fn race_state(State(state): State<SharedState>) -> Json<PublicState> {
    let state = state.read().await;
    Json(PublicState {
        race_number: state.race_number,
        timer: state.timer,
        horses: state.horses,

        user_points: state.user_points,
        user_bet: state.user_bet,

        vip: if state.user_points >= 500 {
            format!(
                "VIP access granted! Here is your flag: {}",
                std::env::var("FLAG").unwrap_or_default()
            )
        } else {
            "VIP status requires 500 points".to_string()
        },
    })
}

#[derive(serde::Deserialize)]
struct BetRequest {
    horse: usize,
    amount: u64,
}

async fn bet(
    State(state): State<SharedState>,
    Json(bet_request): Json<BetRequest>,
) -> Result<StatusCode, (StatusCode, String)> {
    let mut state = state.write().await;

    if state.user_bet.is_some() {
        return Err((
            StatusCode::BAD_REQUEST,
            "You have already placed a bet".to_string(),
        ));
    }
    if bet_request.horse >= 16 {
        return Err((StatusCode::BAD_REQUEST, "Invalid horse number".to_string()));
    }
    if bet_request.amount == 0 {
        return Err((
            StatusCode::BAD_REQUEST,
            "Bet amount must be greater than zero".to_string(),
        ));
    }
    if state.user_points < bet_request.amount {
        return Err((
            StatusCode::BAD_REQUEST,
            "Not enough points to place the bet".to_string(),
        ));
    }

    state.user_bet = Some((bet_request.horse, bet_request.amount));
    state.user_points -= bet_request.amount;

    Ok(StatusCode::OK)
}
