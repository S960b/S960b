# Cybersecurity Portfolio

Cybersecurity / Penetration Testing / CTF

This repository contains my practical cybersecurity work: authorized security assessments and CTF writeups.

## Pentest

Authorized penetration testing and security assessment case studies.

- [API Security Assessment - GigaTool / MultiTool Gateway](pentest/gigatool/) - authorized black-box pentest of the backend API perimeter: token handling and DPoP binding, rate limiting, business logic and API issues

## CTF

Writeups and technical notes from CTF competitions, structured as `ctf/<event>/<type>/<challenge>`.

- [pwnsec / Web - PHault: blind SQL injection](ctf/pwnsec/web/)


## Research

Quantitative market microstructure research (authorized, public market data only).

- [SafeTrade Price-Lead Research](research/safetrade/) - does a small exchange (SafeTrade)
  lag the top-3 spot venues (Binance/OKX/Bybit)? Latency research pipeline: concurrent
  public-data collection, causal oracle (median of 3 external mids), premium b(t),
  reach-to-target tests (informational vs executable), paper simulator (virtual 100 USDT,
  taker/taker), quality dashboard. No trading, no keys, no secrets.
- [Paper seven-day run - status](research/paper_seven_day/STATUS.md) - live status of the
  experimental 7-day paper runner (SOLUSDT/BTCUSDT Donchian, systemd paper-seven-day +
  checkpoint timer). Note: its branch `chatgpt/paper-seven-day-2026-10-08` is NOT merged;
  the status file lives on `main` so a fresh agent can find the recovery steps.

## Automation (personal daily ops)

- [LinkedIn daily posting](linkedin/) - autonomous daily LinkedIn post cycle (research,
  fact-gates, official Posts API publish, Telegram report) via Hermes cron + a resilient
  OS-cron wrapper. Includes the full from-zero restore guide. Personal identification is
  masked for the public repo.

## About

I am currently transitioning into cybersecurity after many years of technical experience with ISP networks and cryptocurrency mining.

My main interests are penetration testing, cybersecurity, CTF, Linux, networking and AI-assisted security research.